"""Preprocessors: pd.Series child with an action sequence & forward method"""
import torch
from opensoundscape.helpers import make_clip_df
import torch
import numpy as np
import pandas as pd
from pathlib import Path
import warnings
import copy


class AudioFileDataset(torch.utils.data.Dataset):
    """Base class for audio datasets with OpenSoundscape (use in place of torch Dataset)

    Custom Dataset classes should subclass this class or its children.

    Datasets in OpenSoundscape contain a Preprocessor object which is
    responsible for the procedure of generating a sample for a given input.
    The DataLoader handles a dataframe of samples (and potentially labels) and
    uses a Preprocessor to generate samples from them.

    Args:
        samples:
            the files to generate predictions for. Can be:
            - a dataframe with index containing audio paths, OR
            - a dataframe with multi-index of (path,start_time,end_time) per clip, OR
            - a list or np.ndarray of audio file paths

            Notes for input dataframe:
             - df must have audio paths in the index.
             - If label_df has labels, the class names should be the columns, and
            the values of each row should be 0 or 1.
             - If data does not have labels, label_df will have no columns
        preprocessor:
            an object of BasePreprocessor or its children which defines
            the operations to perform on input samples
        return_labels:
            if True, the __getitem__ method will return {X:sample,y:labels}
            If False, the __getitem__ method will return {X:sample}
            If label_df has no labels (no columns), use return_labels=False
            [default: True]

    Raises:
        PreprocessingError if exception is raised during __getitem__

    Effects:
        self.unsafe_samples will contain a list of paths that did not successfully
            produce a list of clips with start/end times, if split_files_into_clips=True
    """

    def __init__(
        self, samples, preprocessor, return_labels=True, bypass_augmentations=False
    ):

        ## Input Validation ##

        # validate type of samples: list, np array, or df
        assert type(samples) in (list, np.ndarray, pd.DataFrame,), (
            f"samples must be type list/np.ndarray of file paths, ",
            f"or pd.DataFrame with index containing path (or multi-index of "
            f"path, start_time, end_time). Got {type(samples)}.",
        )
        if type(samples) == list or type(samples) == np.ndarray:
            df = pd.DataFrame(index=samples)
        elif type(samples) == pd.DataFrame:
            # can either have index of file path or multi-index (file_path,start_time,end_time)
            df = samples
        # if the dataframe has a multi-index, it should be (file,start_time,end_time)
        self.has_clips = type(df.index) == pd.core.indexes.multi.MultiIndex
        if self.has_clips:
            assert list(df.index.names) == [
                "file",
                "start_time",
                "end_time",
            ], "multi-index must be ('file','start_time','end_time')"

        # give helpful warnings for incorret df, but don't raise Exception
        first_path = df.index[0][0] if self.has_clips else df.index[0]
        if len(df) > 0 and not Path(first_path).exists():
            warnings.warn(
                "Index of dataframe passed to "
                f"preprocessor must be a file path. Got {df.index[0]}."
            )
        if return_labels and len(df.columns) == 0:
            warnings.warn("return_labels=True but df has no columns!")
        elif len(df) > 0 and return_labels and not df.values[0, 0] in (0, 1):
            warnings.warn(
                "if return_labels=True, label_df must have labels that take values of 0 and 1"
            )

        if len(df) == 0:
            warnings.warn("Zero samples!")

        self.classes = df.columns
        self.label_df = df
        self.return_labels = return_labels
        self.preprocessor = preprocessor
        self.unsafe_samples = []

        # if True skips Actions with .is_augmentation=True
        self.bypass_augmentations = bypass_augmentations

    def __len__(self):
        return self.label_df.shape[0]

    def __getitem__(self, idx, break_on_key=None, break_on_type=None):

        label_df_row = self.label_df.iloc[idx]

        # preprocessor.forward will raise PreprocessingError if something fails
        # the preprocessor handles label_df_row having index of file or (file,start_time,end_time)
        x, sample_info = self.preprocessor.forward(
            label_df_row,
            bypass_augmentations=self.bypass_augmentations,
            break_on_key=break_on_key,
            break_on_type=break_on_type,
        )

        # Return sample & label pairs (training/validation)
        if self.return_labels:
            labels = torch.from_numpy(sample_info["_labels"].values)
            return {"X": x, "y": labels}
        else:  # Return sample only (prediction)
            return {"X": x}

    def __repr__(self):
        return f"{self.__class__} object with preprocessor: {self.preprocessor}"

    def class_counts(self):
        """count number of each label"""
        labels = self.label_df.columns
        counts = np.sum(self.label_df.values, 0)
        return labels, counts

    def sample(self, **kwargs):
        """out-of-place random sample

        creates copy of object with n rows randomly sampled from label_df

        Args: see pandas.DataFrame.sample()

        Returns:
            a new dataset object
        """
        new_ds = copy.deepcopy(self)
        new_ds.label_df = new_ds.label_df.sample(**kwargs)
        return new_ds

    def head(self, n=5):
        """out-of-place copy of first n samples

        performs df.head(n) on self.label_df

        Args:
            n: number of first samples to return, see pandas.DataFrame.head()
            [default: 5]

        Returns:
            a new dataset object
        """
        new_ds = copy.deepcopy(self)
        new_ds.label_df = new_ds.label_df.head(n)
        return new_ds


class AudioSplittingDataset(AudioFileDataset):
    """class to load clips of longer files rather than one sample per file

    Internally creates even-lengthed clips split from long audio files.

    Does not currently support passing labels

    Args:
        see AudioFileDataset and make_clip_df
    """

    def __init__(self, samples, preprocessor, overlap_fraction=0, final_clip=None):
        super(AudioSplittingDataset, self).__init__(
            samples=samples, preprocessor=preprocessor, return_labels=False
        )

        # create clip df
        clip_times_df, self.unsafe_samples = make_clip_df(
            self.label_df.index.values,
            preprocessor.sample_duration,
            overlap_fraction * preprocessor.sample_duration,
            final_clip,
        )
        # clip_times_df might be None if no files succeeded, make empty df
        if clip_times_df is None:
            clip_times_df = pd.DataFrame(
                columns=["file", "start_time", "end_time"] + self.classes
            )

        # update "label_df" with multi-index (file,start_time,end_time)
        # using clip file names and start/end times from clip_times_df
        self.label_df = clip_times_df.reset_index().set_index(
            ["file", "start_time", "end_time"]
        )[[]]
