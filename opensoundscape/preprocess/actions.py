"""Actions for augmentation and preprocessing pipelines

This module contains Action classes which act as the elements in
Preprocessor pipelines. Action classes have go(), on(), off(), and set()
methods. They take a single sample of a specific type and return the transformed
or augmented sample, which may or may not be the same type as the original.

See the preprocessor module and Preprocessing tutorial
for details on how to use and create your own actions.
"""
import numpy as np
from PIL import Image
import random
from pathlib import Path
from time import time
import os
from torchvision import transforms
import torch
from torchvision.utils import save_image
import warnings

from opensoundscape.audio import Audio
from opensoundscape.spectrogram import Spectrogram, MelSpectrogram
from opensoundscape.preprocess import tensor_augment as tensaug
from opensoundscape.preprocess.utils import PreprocessingError, get_args, get_reqd_args
from opensoundscape.preprocess.utils import _run_pipeline


class BaseAction:
    """Parent class for all Actions (used in Preprocessor pipelines)

    New actions should subclass this class.

    Subclasses should set `self.requires_labels = True` if go() expects (X,y)
    instead of (X). y is a row of a dataframe (a pd.Series) with index (.name)
    = original file path, columns=class names, values=labels (0,1). X is the
    sample, and can be of various types (path, Audio, Spectrogram, Tensor, etc).
    See ImgOverlay for an example of an Action that uses labels.
    """

    def __init__(self):
        self.params = {}
        self.extra_args = []
        self.returns_labels = False
        self.is_augmentation = False

    def __repr__(self):
        return f"Action"

    def go(self, x, **kwargs):
        return x

    def set(self, **kwargs):
        """only allow keys that exist in self.params"""
        unmatched_args = set(list(kwargs.keys())) - set(list(self.params.keys()))
        assert unmatched_args == set(
            []
        ), f"unexpected arguments: {unmatched_args}. The valid arguments are: \n{self.params}"
        self.params.update(kwargs)

    def get(self, arg):
        return self.params[arg]


class Action(BaseAction):
    """Action class for an arbitrary function

    The function must take the sample as the first argument

    Note that this allows two use cases:
    (A) regular function that takes an input object as first argument
        eg. Audio.from_file(path,**kwargs)
    (B) method of a class, which takes 'self' as the first argument,
        eg. Spectrogram.bandpass(self,**kwargs)

    Other arguments are an arbitrary list of kwargs.
    """

    def __init__(self, fn, extra_args=[], **kwargs):
        super(Action, self).__init__()

        self.action_fn = fn
        # args that vary for each sample, will be passed from preprocessor
        self.extra_args = extra_args

        # query action_fn for arguments and default values
        self.params = get_args(self.action_fn)

        # whether the first argument is 'self' or the incoming object,
        # we remove it from the params dict
        self.params.pop(next(iter(self.params)))

        # update self.params with any user-provided parameters
        self.set(**kwargs)

        # make sure all required args are given (skipping the first, which will be provided by go)
        unmatched_reqd_args = (
            set(get_reqd_args(self.action_fn)[1:])
            - set(list(kwargs.keys()))
            - set(self.extra_args)
        )

        assert unmatched_reqd_args == set(
            []
        ), f"These required arguments were not provided: {unmatched_reqd_args}"

    def __repr__(self):
        return f"Action calling {self.action_fn}"

    def go(self, x, **kwargs):
        # incidentally(?), the syntax is the same regardless of whether
        # first argument is "self" (for a class method) or not
        return self.action_fn(x, **dict(self.params, **kwargs))


class Augmentation(Action):
    """Subclass of Action with self.is_augmentation=True"""

    def __init__(self, fn, extra_args=[], **kwargs):
        super(Augmentation, self).__init__(fn, extra_args=extra_args, **kwargs)
        self.is_augmentation = True


class AudioClipLoader(Action):
    """Action to load clips from an audio file

    Loads an audio file or part of a file to an Audio object.
    Will load entire audio file if _start_time and _end_time are None.
    see Audio.from_file() for documentation.

    Args:
        see Audio.from_file()
    """

    def __init__(self, **kwargs):
        super(AudioClipLoader, self).__init__(
            Audio.from_file, extra_args=["_start_time", "_sample_duration"], **kwargs
        )

    def go(self, path, _start_time, _sample_duration, **kwargs):
        offset = 0 if _start_time is None else _start_time
        # only trim to _sample_duration if _start_time is provided
        # ie, we are loading clips from a long audio file
        duration = None if _start_time is None else _sample_duration
        return self.action_fn(path, offset=offset, duration=duration, **kwargs)


class AudioTrim(Action):
    """Action to trim/extend audio to desired length

    Args:
        see actions.trim_audio
    """

    def __init__(self, **kwargs):
        super(AudioTrim, self).__init__(
            trim_audio, extra_args=["_sample_duration"], **kwargs
        )


class AudioRandomTrim(Augmentation):
    """Augmentation to trim a random section from a longer audio clip

    Randomly selects a section of a longer audio clip.

    Args:
        see actions.trim_audio
        do not specify `random_trim`, it is set to True by default
    """

    def __init__(self, **kwargs):
        super(AudioRandomTrim, self).__init__(
            trim_audio, extra_args=["_sample_duration"], random_trim=True, **kwargs
        )


def trim_audio(audio, _sample_duration, extend=True, random_trim=False):
    """trim audio clips (Audio -> Audio)

    Trims an audio file to desired length
    Allows audio to be trimmed from start or from a random time
    Optionally extends audio shorter than clip_length with silence

    Args:
        audio: Audio object
        _sample_duration: desired final length (sec); if None, no trim is performed
        extend: if True, clips shorter than _sample_duration are
            extended with silence to required length
        random_trim: if True, a random segment of length _sample_duration is chosen
            from the input audio. If False, the file is trimmed from 0 seconds
            to _sample_duration seconds.

    Returns:
        trimmed audio
    """
    if len(audio.samples) == 0:
        raise ValueError("recieved zero-length audio")

    if _sample_duration is not None:
        if audio.duration() <= _sample_duration:
            # input audio is not as long as desired length
            if extend:  # extend clip sith silence
                audio = audio.extend(_sample_duration)
            else:
                raise ValueError(
                    f"the length of the original file ({audio.duration()} "
                    f"sec) was less than the length to extract "
                    f"({_sample_duration} sec). To extend short "
                    f"clips, use extend=True"
                )
        if random_trim:
            # uniformly randomly choose clip time from full audio
            extra_time = audio.duration() - _sample_duration
            start_time = np.random.uniform() * extra_time
        else:
            start_time = 0

        end_time = start_time + _sample_duration
        audio = audio.trim(start_time, end_time)

    return audio


#
# class SaveTensorToDisk(BaseAction):
#     """save a torch Tensor to disk (Tensor -> Tensor)
#
#     Requires x_labels because the index of the label-row (.name)
#     gives the original file name for this sample.
#
#     Uses torchvision.utils.save_image. Creates save_path dir if it doesn't exist
#
#     Args:
#         save_path: a directory where tensor will be saved
#     """
#
#     def __init__(self, save_path, **kwargs):
#         super(SaveTensorToDisk, self).__init__(**kwargs)
#         self.requires_labels = True
#         # make this directory if it doesnt exist yet
#         self.save_path = Path(save_path)
#         self.save_path.mkdir(parents=True, exist_ok=True)
#
#     def go(self, x, x_labels):
#         """we require x_labels because the .name gives origin file name"""
#         filename = os.path.basename(x_labels.name) + f"_{time()}.png"
#         path = Path.joinpath(self.save_path, filename)
#         save_image(x, path)
#         return x, x_labels


def torch_color_jitter(tensor, brightness=0.3, contrast=0.3, saturation=0.3, hue=0):
    """Wraps torchvision.transforms.ColorJitter

    (Tensor -> Tensor) or (PIL Img -> PIL Img)

    Args:
        tensor: input sample
        brightness=0.3
        contrast=0.3
        saturation=0.3
        hue=0

    Returns:
        modified tensor
    """
    transform = transforms.Compose(
        [
            transforms.ColorJitter(
                brightness=brightness, contrast=contrast, saturation=saturation, hue=hue
            )
        ]
    )
    return transform(x)


def torch_random_affine(tensor, degrees=0, translate=(0.3, 0.1), fill=0):
    """Wraps for torchvision.transforms.RandomAffine

    (Tensor -> Tensor) or (PIL Img -> PIL Img)

    Args:
        tensor: torch.Tensor input saple
        degrees = 0
        translate = (0.3, 0.1)
        fill = 0-255, duplicated across channels

    Returns:
        modified tensor

    Note: If applying per-image normalization, we recommend applying
    RandomAffine after image normalization. In this case, an intermediate gray
    value is ~0. If normalization is applied after RandomAffine on a PIL image,
    use an intermediate fill color such as (122,122,122).
    """

    channels = tensor.shape[-3]
    fill = [fill] * channels

    transform = transforms.Compose(
        [transforms.RandomAffine(degrees=degrees, translate=translate, fill=fill)]
    )
    return transform(tensor)


def image_to_tensor(img, greyscale=False):
    """Convert PIL image to RGB or greyscale Tensor (PIL.Image -> Tensor)

    convert PIL.Image w/range [0,255] to torch Tensor w/range [0,1]

    Args:
        img: PIL.Image
        greyscale: if False, converts image to RGB (3 channels).
            If True, converts image to one channel.
    """
    if greyscale:
        img = img.convert("L")
    else:
        img = img.convert("RGB")

    transform = transforms.Compose([transforms.ToTensor()])
    return transform(img)


def scale_tensor(tensor, input_mean=0.5, input_std=0.5):
    """linear scaling of tensor values using torch.transforms.Normalize

    (Tensor->Tensor)

    WARNING: This does not perform per-image normalization. Instead,
    it takes as arguments a fixed u and s, ie for the entire dataset,
    and performs X=(X-input_mean)/input_std.

    Args:
        input_mean: mean of input sample pixels (average across dataset)
        input_std: standard deviation of input sample pixels (average across dataset)
        (these are NOT the target mu and sd, but the original mu and sd of img
        for which the output will have mu=0, std=1)

    Returns:
        modified tensor
    """
    transform = transforms.Compose([transforms.Normalize(input_mean, input_std)])
    return transform(tensor)


# def time_warp(tensor, warp_amount=5):
#     """Time warp is an experimental augmentation that creates a tilted image.
#
#     Args:
#         tensor: sample to augment
#         warp_amount: use higher values for more skew and offset (experimental)
#
#     Note: this augmentation reduces multi-channel images to greyscale and duplicates the
#     result across the channels.
#
#     """
#     channels = tensor.shape[0]
#     # add "batch" dimension to tensor and use just first channel
#     tensor = tensor[0, :, :].unsqueeze(0).unsqueeze(0)
#     # perform transform
#     tensor = tensaug.time_warp(tensor.clone(), W=self.params["warp_amount"])
#     # remove "batch" dimension
#     tensor = tensor[0, :]
#     # Copy 1 channel to 3 RGB channels
#     tensor = torch.cat([tensor] * n_channels, dim=0)  # dim=1)
#     return tensor


def time_mask(tensor, max_masks=3, max_width=0.2):
    """add random vertical bars over image (Tensor -> Tensor)

    Args:
        tensor: input Torch.tensor sample
        max_masks: maximum number of vertical bars [default: 3]
        max_width: maximum size of bars as fraction of image width

    Returns:
        augmented tensor
    """

    # convert max_width from fraction of image to pixels
    max_width_px = int(tensor.shape[-1] * max_width)

    # add "batch" dimension expected by tensaug
    tensor = tensor.unsqueeze(0)

    # perform transform
    tensor = tensaug.time_mask(tensor, T=max_width_px, max_masks=max_masks)

    # remove "batch" dimension
    tensor = tensor.squeeze(0)

    return tensor


def frequency_mask(tensor, max_masks=3, max_width=0.2):
    """add random horizontal bars over Tensor

    Args:
        tensor: input Torch.tensor sample
        max_masks: max number of horizontal bars [default: 3]
        max_width: maximum size of horizontal bars as fraction of image height

    Returns:
        augmented tensor
    """

    # convert max_width from fraction of image to pixels
    max_width_px = int(tensor.shape[-2] * max_width)

    # add "batch" dimension expected by tensaug
    tensor = tensor.unsqueeze(0)

    # perform transform
    tensor = tensaug.freq_mask(tensor, F=max_width_px, max_masks=max_masks)

    # remove "batch" dimension
    tensor = tensor.squeeze(0)

    return tensor


# class TensorAugment(BaseAction):
#     """combination of 3 augmentations with hard-coded parameters
#
#     time warp, time mask, and frequency mask
#
#     use (bool) time_warp, time_mask, freq_mask to turn each on/off
#
#     Note: This function reduces the image to greyscale then duplicates the
#     image across the 3 channels
#     """
#
#     def __init__(self, **kwargs):
#         super(TensorAugment, self).__init__(**kwargs)
#
#         # default parameters
#         self.params["time_warp"] = True
#         self.params["time_mask"] = True
#         self.params["freq_mask"] = True
#
#         # add parameters passed to __init__
#         self.params.update(kwargs)
#
#     def go(self, x):
#         """torch Tensor in, torch Tensor out"""
#         # add "batch" dimension to tensor and keep just first channel
#         x = x[0, :, :].unsqueeze(0).unsqueeze(0)  # was: X = X[:,0].unsqueeze(1)
#         x = tensaug.time_warp(x.clone(), W=10)
#         x = tensaug.time_mask(x, T=50, max_masks=5)
#         x = tensaug.freq_mask(x, F=50, max_masks=5)
#         # remove "batch" dimension
#         x = x[0, :]
#         # Copy 1 channel to 3 RGB channels
#         x = torch.cat([x] * 3, dim=0)  # dim=1)
#
#         return x


def tensor_add_noise(tensor, std=1):
    """Add gaussian noise to sample (Tensor -> Tensor)

    Args:
        std: standard deviation for Gaussian noise [default: 1]

    Note: be aware that scaling before/after this action will change the
    effect of a fixed stdev Gaussian noise
    """
    noise = torch.empty_like(tensor).normal_(mean=0, std=std)
    return tensor + noise


class ImgOverlay(Augmentation):
    """Action Class for augmentation that overlays samples on eachother

    Required Args:
        overlay_df: dataframe of audio files (index) and labels to use for overlay
        update_labels (bool): if True, labels of sample are updated to include
            labels of overlayed sample

    See overlay_image() for other arguments and default values.
    """

    def __init__(self, **kwargs):

        super(ImgOverlay, self).__init__(
            overlay_image, extra_args=["_labels", "_pipeline"], **kwargs
        )

        self.returns_labels = True

        overlay_df = kwargs["overlay_df"]
        overlay_df = overlay_df[~overlay_df.index.duplicated()]  # remove duplicates

        # warn the user if using "different" as overlay_class and "different" is one of the model classes
        if (
            "different" in overlay_df.columns
            and "overlay_class" in kwargs
            and kwargs["overlay_class"] == "different"
        ):
            warnings.warn(
                "class name `different` was in columns, but using "
                "kwarg overlay_class='different' has specific behavior and will "
                "not specifically choose files from the `different` class. "
                "Consider renaming the `different` class. "
            )

        # move overlay_df from params to its own space, so that it doesn't display with print(params)
        self.overlay_df = overlay_df
        self.params.pop("overlay_df")  # removes it

    def go(self, x, **kwargs):
        return self.action_fn(
            x, overlay_df=self.overlay_df, **dict(self.params, **kwargs)
        )


def overlay_image(
    x,
    _labels,
    _pipeline,
    overlay_df,
    update_labels,
    # default overlay parameters
    overlay_class=None,  # or 'different' or specific class
    overlay_prob=1,
    max_overlay_num=1,
    overlay_weight=0.5,  # allows float or range)
):
    """iteratively overlay images on top of eachother

    Overlays images from overlay_df on top of the sample with probability
    overlay_prob until stopping condition.
    If necessary, trims overlay audio to the length of the input audio.
    Overlays the images on top of each other with a weight.

    Overlays can be used in a few general ways:
        1. a separate df where any file can be overlayed (overlay_class=None)
        2. same df as training, where the overlay class is "different" ie,
            does not contain overlapping labels with the original sample
        3. same df as training, where samples from a specific class are used
            for overlays

    Args:
        overlay_df: a labels dataframe with audio files as the index and
            classes as columns
        _labels: labels of the original sample
        _pipeline: the preprocessing pipeline to load audio -> image
        update_labels: if True, add overlayed sample's labels to original sample
        overlay_class: how to choose files from overlay_df to overlay
            Options [default: "different"]:
            None - Randomly select any file from overlay_df
            "different" - Select a random file from overlay_df containing none
                of the classes this file contains
            specific class name - always choose files from this class
        overlay_prob: the probability of applying each subsequent overlay
        max_overlay_num: the maximum number of samples to overlay on original
            - for example, if overlay_prob = 0.5 and max_overlay_num=2,
                1/2 of images will recieve 1 overlay and 1/4 will recieve an
                additional second overlay
        overlay_weight: can be a float between 0-1 or range of floats (chooses
            randomly from within range) such as [0.1,0.7].
            An overlay_weight <0.5 means more emphasis on original image.

    Returns:
        overlayed sample, (possibly updated) labels

    """
    ##  INPUT VALIDATION ##
    assert (
        overlay_class in ["different", None] or overlay_class in overlay_df.columns
    ), (
        "overlay_class must be 'different' or None or in overlay_df.columns. "
        f"got {overlay_class}"
    )
    assert (overlay_prob <= 1) and (overlay_prob >= 0), (
        "overlay_prob" f"should be in range (0,1), was {overlay_prob}"
    )

    weight_error = f"overlay_weight should be between 0 and 1, was {overlay_weight}"

    if hasattr(overlay_weight, "__iter__"):
        assert (
            len(overlay_weight) == 2
        ), "must provide a float or a range of min,max values for overlay_weight"
        assert (
            overlay_weight[1] > overlay_weight[0]
        ), "second value must be greater than first for overlay_weight"
        for w in overlay_weight:
            assert w < 1 and w > 0, weight_error
    else:
        assert overlay_weight < 1 and overlay_weight > 0, weight_error

    if overlay_class is not None:
        assert (
            len(overlay_df.columns) > 0
        ), "overlay_df must have labels if overlay_class is specified"
        if overlay_class != "different":  # user specified a single class
            assert (
                np.sum(overlay_df[overlay_class]) > 0
            ), "overlay_df did not contain positive labels for overlay_class"

    if len(overlay_df.columns) > 0:
        assert list(overlay_df.columns) == list(
            _labels.index
        ), "overlay_df mast have same columns as sample's _labels or no columns"

    ## OVERLAY ##
    # iteratively perform overlays until stopping condition
    # each time, there is an overlay_prob probability of another overlay
    # up to a max number of max_overlay_num overlays
    overlays_performed = 0
    while overlay_prob > np.random.uniform() and overlays_performed < max_overlay_num:
        overlays_performed += 1

        # lets pick a sample based on rules
        if overlay_class is None:
            # choose any file from the overlay_df
            overlay_path = random.choice(overlay_df.index)

        elif overlay_class == "different":
            # Select a random file containing none of the classes this file contains
            # because the overlay_df might be huge and sparse, we randomly
            # choose row until one fits criterea rather than filtering overlay_df
            # TODO: revisit this choice
            good_choice = False
            attempt_counter = 0
            max_attempts = 100  # if we try this many times, raise error
            while (not good_choice) and (attempt_counter < max_attempts):
                attempt_counter += 1

                # choose a random sample from the overlay df
                candidate_idx = random.randint(0, len(overlay_df) - 1)

                # check if this candidate sample has zero overlapping labels
                label_intersection = np.logical_and(
                    overlay_df.values[candidate_idx, :], _labels.values
                )
                good_choice = sum(label_intersection) == 0

            if not good_choice:  # tried max_attempts samples, none worked
                warnings.warn("No samples found with non-overlapping labels")
                continue

            overlay_path = overlay_df.index[candidate_idx]

        else:
            # Select a random file from a class of choice (may be slow -
            # however, in the case of a fixed overlay class, we could
            # pass an overlay_df containing only that class)
            choose_from = overlay_df[overlay_df[overlay_class] == 1]
            overlay_path = np.random.choice(choose_from.index.values)

        # now we have picked a file to overlay (overlay_path)
        # we also know its labels, if we need them
        overlay_row = overlay_df.loc[overlay_path]
        overlay_labels = overlay_row.values

        # update the labels with new classes
        if update_labels and len(overlay_labels) > 0:
            # update labels as union of both files' labels
            _labels.values[:] = np.logical_or(_labels.values, overlay_labels).astype(
                int
            )

        # now we need to run the pipeline to do everything up until the ImgOverlay step
        # create a preprocessor for loading the overlay samples
        x2, sample_info = _run_pipeline(
            _pipeline, overlay_row, break_on_type=ImgOverlay
        )

        # now we blend the two tensors together with a weighted average
        # Select weight of overlay; <0.5 means more emphasis on original image
        # Supports uniform-random selection from a range of weights eg [0.1,0.7]
        weight = overlay_weight
        if hasattr(weight, "__iter__"):
            assert (
                len(weight) == 2
            ), f"overlay_weight must specify a single value or range of 2 values, got {overlay_weight}"
            weight = random.uniform(weight[0], weight[1])

        # use a weighted sum to overlay (blend) the images
        x = x * (1 - weight) + x2 * weight

    return x, _labels
