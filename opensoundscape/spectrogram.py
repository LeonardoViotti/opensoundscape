#!/usr/bin/env python3
""" spectrogram.py: Utilities for dealing with spectrograms
"""

from scipy import signal
import numpy as np
from opensoundscape.audio import Audio
from opensoundscape.helpers import min_max_scale, linear_scale
import warnings
from matplotlib.cm import get_cmap
import librosa.filters


class Spectrogram:
    """Immutable spectrogram container

    Can be initialized directly from spectrogram, frequency, and time values
    or created from an Audio object using the .from_audio() method.

    Attributes:
        frequencies: (list) discrete frequency bins generated by fft
        times: (list) time from beginning of file to the center of each window
        spectrogram: a 2d array containing 10*log10(fft) for each time window
        decibel_limits: minimum and maximum decibel values in .spectrogram
        window_samples: number of samples per window when spec was created
            [default: none]
        overlap_samples: number of samples overlapped in consecutive windows
            when spec was created
            [default: none]
        window_type: window fn used to make spectrogram, eg 'hann'
            [default: none]
        audio_sample_rate: sample rate of audio from which spec was created
            [default: none]
        scaling:Selects between computing the power spectral density (‘density’) where Sxx has units of V**2/Hz and computing the power spectrum (‘spectrum’) where Sxx has units of V**2, if x is measured in V and fs is measured in Hz.
            [default: spectrum]

    """

    __slots__ = (
        "frequencies",
        "times",
        "spectrogram",
        "decibel_limits",
        "window_samples",
        "overlap_samples",
        "window_type",
        "audio_sample_rate",
        "scaling",
    )

    def __init__(
        self,
        spectrogram,
        frequencies,
        times,
        decibel_limits,
        window_samples=None,
        overlap_samples=None,
        window_type=None,
        audio_sample_rate=None,
        scaling=None,
    ):
        if not isinstance(spectrogram, np.ndarray):
            raise TypeError(
                f"Spectrogram.spectrogram should be a np.ndarray [shape=(n, m)]. Got {spectrogram.__class__}"
            )
        if not isinstance(frequencies, np.ndarray):
            raise TypeError(
                f"Spectrogram.frequencies should be an np.ndarray [shape=(n,)]. Got {frequencies.__class__}"
            )
        if not isinstance(times, np.ndarray):
            raise TypeError(
                f"Spectrogram.times should be an np.ndarray [shape=(m,)]. Got {times.__class__}"
            )
        if not isinstance(decibel_limits, tuple):
            raise TypeError(
                f"Spectrogram.decibel_limits should be a tuple [length=2]. Got {decibel_limits.__class__}"
            )

        if spectrogram.ndim != 2:
            raise TypeError(
                f"spectrogram should be a np.ndarray [shape=(n, m)]. Got {spectrogram.shape}"
            )
        if frequencies.ndim != 1:
            raise TypeError(
                f"frequencies should be an np.ndarray [shape=(n,)]. Got {frequencies.shape}"
            )
        if times.ndim != 1:
            raise TypeError(
                f"times should be an np.ndarray [shape=(m,)]. Got {times.shape}"
            )
        if len(decibel_limits) != 2:
            raise TypeError(
                f"decibel_limits should be a tuple [length=2]. Got {len(decibel_limits)}"
            )

        if spectrogram.shape != (frequencies.shape[0], times.shape[0]):
            raise TypeError(
                f"Dimension mismatch, spectrogram.shape: {spectrogram.shape}, frequencies.shape: {frequencies.shape}, times.shape: {times.shape}"
            )

        super(Spectrogram, self).__setattr__("frequencies", frequencies)
        super(Spectrogram, self).__setattr__("times", times)
        super(Spectrogram, self).__setattr__("spectrogram", spectrogram)
        super(Spectrogram, self).__setattr__("decibel_limits", decibel_limits)
        super(Spectrogram, self).__setattr__("window_samples", window_samples)
        super(Spectrogram, self).__setattr__("overlap_samples", overlap_samples)
        super(Spectrogram, self).__setattr__("window_type", window_type)
        super(Spectrogram, self).__setattr__("audio_sample_rate", audio_sample_rate)
        super(Spectrogram, self).__setattr__("scaling", scaling)

    @classmethod
    def from_audio(
        cls,
        audio,
        window_type="hann",
        window_samples=None,
        window_length_sec=None,
        overlap_samples=None,
        overlap_fraction=None,
        fft_size=None,
        decibel_limits=(-100, -20),
        dB_scale=True,
        scaling="spectrum",
    ):
        """
        create a Spectrogram object from an Audio object

        Args:
            window_type="hann": see scipy.signal.spectrogram docs for description of window parameter
            window_samples: number of audio samples per spectrogram window (pixel)
                - Defaults to 512 if window_samples and window_length_sec are None
                - Note: cannot specify both window_samples and window_length_sec
            window_length_sec: length of a single window in seconds
                - Note: cannot specify both window_samples and window_length_sec
                - Warning: specifying this parameter often results in less efficient
                    spectrogram computation because window_samples will not be
                    a power of 2.
            overlap_samples: number of samples shared by consecutive windows
                - Note: must not specify both overlap_samples and overlap_fraction
            overlap_fraction: fractional temporal overlap between consecutive windows
                - Defaults to 0.5 if overlap_samples and overlap_fraction are None
                - Note: cannot specify both overlap_samples and overlap_fraction
            fft_size: see scipy.signal.spectrogram's `nfft` parameter
            decibel_limits: limit the dB values to (min,max) (lower values set to min, higher values set to max)
            dB_scale: If True, rescales values to decibels, x=10*log10(x)
                - if dB_scale is False, decibel_limits is ignored
            scaling="spectrum": see scipy.signal.spectrogram docs for description of scaling parameter


        Returns:
            opensoundscape.spectrogram.Spectrogram object
        """
        if not isinstance(audio, Audio):
            raise TypeError("Class method expects Audio class as input")

        # determine window_samples
        if window_samples is not None and window_length_sec is not None:
            raise ValueError(
                "You may not specify both `window_samples` and `window_length_sec`"
            )
        elif window_samples is None and window_length_sec is None:
            window_samples = 512  # defaults to 512 samples
        elif window_length_sec is not None:
            window_samples = int(audio.sample_rate * window_length_sec)
        # else: use user-provided window_samples argument

        # determine overlap_samples
        if overlap_samples is not None and overlap_fraction is not None:
            raise ValueError(
                "You may not specify both `overlap_samples` and `overlap_fraction`"
            )
        elif overlap_samples is None and overlap_fraction is None:
            # default is 50% overlap
            overlap_samples = window_samples // 2
        elif overlap_fraction is not None:
            assert (
                overlap_fraction >= 0 and overlap_fraction < 1
            ), "overlap_fraction must be >=0 and <1"
            overlap_samples = int(window_samples * overlap_fraction)
        # else: use the provided overlap_samples argument

        frequencies, times, spectrogram = signal.spectrogram(
            audio.samples,
            audio.sample_rate,
            window=window_type,
            nperseg=int(window_samples),
            noverlap=int(overlap_samples),
            nfft=fft_size,
            scaling=scaling,
        )

        # convert to decibels
        # -> avoid RuntimeWarning by setting negative values to -np.inf (mapped to min_db later)
        if dB_scale:
            spectrogram = 10 * np.log10(
                spectrogram,
                where=spectrogram > 0,
                out=np.full(spectrogram.shape, -np.inf),
            )

            # limit the decibel range (-100 to -20 dB by default)
            # values below lower limit set to lower limit, values above upper limit set to uper limit
            min_db, max_db = decibel_limits
            spectrogram[spectrogram > max_db] = max_db
            spectrogram[spectrogram < min_db] = min_db

        new_obj = cls(
            spectrogram,
            frequencies,
            times,
            decibel_limits,
            window_samples=window_samples,
            overlap_samples=overlap_samples,
            window_type=window_type,
            audio_sample_rate=audio.sample_rate,
            scaling=scaling,
        )
        return new_obj

    def __setattr__(self, name, value):
        raise AttributeError("Spectrogram's cannot be modified")

    def __repr__(self):
        return f"<Spectrogram(spectrogram={self.spectrogram.shape}, frequencies={self.frequencies.shape}, times={self.times.shape})>"

    def duration(self):
        """calculate the ammount of time represented in the spectrogram

        Note: time may be shorter than the duration of the audio from which
        the spectrogram was created, because the windows may align in a way
        such that some samples from the end of the original audio were
        discarded
        """
        window_length = self.window_length()
        if window_length is None:
            warnings.warn(
                "spectrogram must have window_length attribute to"
                " accurately calculate duration. Approximating duration."
            )
            return self.times[-1]
        else:
            return self.times[-1] + window_length / 2

    def window_length(self):
        """calculate length of a single fft window, in seconds:"""
        if self.window_samples and self.audio_sample_rate:
            return float(self.window_samples) / self.audio_sample_rate
        return None

    def window_step(self):
        """calculate time difference (sec) between consecutive windows' centers"""
        if self.window_samples and self.overlap_samples and self.audio_sample_rate:
            return (
                float(self.window_samples - self.overlap_samples)
                / self.audio_sample_rate
            )
        return None

    def window_start_times(self):
        """get start times of each window, rather than midpoint times"""
        window_length = self.window_length()
        if window_length is not None:
            return np.array(self.times) - window_length / 2

    def min_max_scale(self, feature_range=(0, 1)):
        """
        Linearly rescale spectrogram values to a range of values using
        in_range as minimum and maximum

        Args:
            feature_range: tuple of (low,high) values for output

        Returns:
            Spectrogram object with values rescaled to feature_range
        """

        if len(feature_range) != 2:
            raise AttributeError(
                "Error: `feature_range` doesn't look like a 2-element tuple?"
            )
        if feature_range[1] < feature_range[0]:
            raise AttributeError("Error: `feature_range` isn't increasing?")

        # use self.__class__ so that child classes can inherit this method
        return self.__class__(
            min_max_scale(self.spectrogram, feature_range=feature_range),
            self.frequencies,
            self.times,
            self.decibel_limits,
        )

    def linear_scale(self, feature_range=(0, 1)):
        """
        Linearly rescale spectrogram values to a range of values
        using in_range as decibel_limits

        Args:
            feature_range: tuple of (low,high) values for output

        Returns:
            Spectrogram object with values rescaled to feature_range
        """

        if len(feature_range) != 2:
            raise AttributeError(
                "Error: `feature_range` doesn't look like a 2-element tuple?"
            )
        if feature_range[1] < feature_range[0]:
            raise AttributeError("Error: `feature_range` isn't increasing?")

        return self.__class__(
            linear_scale(
                self.spectrogram, in_range=self.decibel_limits, out_range=feature_range
            ),
            self.frequencies,
            self.times,
            self.decibel_limits,
        )

    def limit_db_range(self, min_db=-100, max_db=-20):
        """Limit the decibel values of the spectrogram to range from min_db to max_db

        values less than min_db are set to min_db
        values greater than max_db are set to max_db

        similar to Audacity's gain and range parameters

        Args:
            min_db: values lower than this are set to this
            max_db: values higher than this are set to this
        Returns:
            Spectrogram object with db range applied
        """
        if not max_db > min_db:
            raise ValueError(
                f"max_db must be greater than min_db (got max_db={max_db} and min_db={min_db})"
            )

        _spec = self.spectrogram.copy()

        _spec[_spec > max_db] = max_db
        _spec[_spec < min_db] = min_db

        return self.__class__(_spec, self.frequencies, self.times, self.decibel_limits)

    def bandpass(self, min_f, max_f, out_of_bounds_ok=True):
        """extract a frequency band from a spectrogram

        crops the 2-d array of the spectrograms to the desired frequency range

        Args:
            min_f: low frequency in Hz for bandpass
            max_f: high frequency in Hz for bandpass
            out_of_bounds_ok: (bool) if False, raises ValueError if min_f or max_f
                are not within the range of the original spectrogram's frequencies
                [default: True]

        Returns:
            bandpassed spectrogram object

        """

        if min_f >= max_f:
            raise ValueError(
                f"min_f must be less than max_f (got min_f {min_f}, max_f {max_f}"
            )

        if not out_of_bounds_ok:
            # self.frequencies fully coveres the spec's frequency range
            if min_f < min(self.frequencies) or max_f > max(self.frequencies):
                raise ValueError(
                    "with out_of_bounds_ok=False, min_f and max_f must fall"
                    "inside the range of self.frequencies"
                )

        # find indices of the frequencies in spec_freq closest to min_f and max_f
        lowest_index = np.abs(self.frequencies - min_f).argmin()
        highest_index = np.abs(self.frequencies - max_f).argmin()

        # take slices of the spectrogram and spec_freq that fall within desired range
        return self.__class__(
            self.spectrogram[lowest_index : highest_index + 1, :],
            self.frequencies[lowest_index : highest_index + 1],
            self.times,
            self.decibel_limits,
        )

    def trim(self, start_time, end_time):
        """extract a time segment from a spectrogram

        Args:
            start_time: in seconds
            end_time: in seconds

        Returns:
            spectrogram object from extracted time segment

        """

        # find indices of the times in self.times closest to min_t and max_t
        lowest_index = np.abs(self.times - start_time).argmin()
        highest_index = np.abs(self.times - end_time).argmin()

        # take slices of the spectrogram and spec_freq that fall within desired range
        return self.__class__(
            self.spectrogram[:, lowest_index : highest_index + 1],
            self.frequencies,
            self.times[lowest_index : highest_index + 1],
            self.decibel_limits,
        )

    def plot(self, inline=True, fname=None, show_colorbar=False):
        """Plot the spectrogram with matplotlib.pyplot

        Args:
            inline=True:
            fname=None: specify a string path to save the plot to (ending in .png/.pdf)
            show_colorbar: include image legend colorbar from pyplot
        """
        from matplotlib import pyplot as plt
        import matplotlib.colors

        norm = matplotlib.colors.Normalize(
            vmin=self.decibel_limits[0], vmax=self.decibel_limits[1]
        )
        plt.pcolormesh(
            self.times,
            self.frequencies,
            self.spectrogram,
            shading="auto",
            cmap="Greys",
            norm=norm,
        )

        plt.xlabel("time (sec)")
        plt.ylabel("frequency (Hz)")
        if show_colorbar:
            plt.colorbar()

        # if fname is not None, save to file path fname
        if fname:
            plt.savefig(fname)

        # if not saving to file, check if a matplotlib backend is available
        if inline:
            import os

            if os.environ.get("MPLBACKEND") is None:
                warnings.warn("MPLBACKEND is 'None' in os.environ. Skipping plot.")
            else:
                plt.show()

    def amplitude(self, freq_range=None):
        """create an amplitude vs time signal from spectrogram

        by summing pixels in the vertical dimension

        Args
            freq_range=None: sum Spectrogrm only in this range of [low, high] frequencies in Hz
            (if None, all frequencies are summed)

        Returns:
            a time-series array of the vertical sum of spectrogram value

        """
        if freq_range is None:
            return np.sum(self.spectrogram, 0)
        else:
            return np.sum(self.bandpass(freq_range[0], freq_range[1]).spectrogram, 0)

    def net_amplitude(
        self, signal_band, reject_bands=None
    ):  # used to be called "net_power_signal" which is misleading (not power)
        """create amplitude signal in signal_band and subtract amplitude from reject_bands

        rescale the signal and reject bands by dividing by their bandwidths in Hz
        (amplitude of each reject_band is divided by the total bandwidth of all reject_bands.
        amplitude of signal_band is divided by badwidth of signal_band. )

        Args:
            signal_band: [low,high] frequency range in Hz (positive contribution)
            reject band: list of [low,high] frequency ranges in Hz (negative contribution)

        return: time-series array of net amplitude"""

        # find the amplitude signal for the desired frequency band
        signal_band_amplitude = self.amplitude(signal_band)

        signal_band_bandwidth = signal_band[1] - signal_band[0]

        # rescale amplitude by 1 / size of frequency band in Hz ("amplitude per unit Hz" ~= color on a spectrogram)
        net_amplitude = signal_band_amplitude / signal_band_bandwidth

        # then subtract the energy in the the reject_bands from the signal_band_amplitude to get net_amplitude
        if not (reject_bands is None):
            # we sum up the sizes of the rejection bands (to not overweight signal_band)
            reject_bands = np.array(reject_bands)
            reject_bands_total_bandwidth = sum(reject_bands[:, 1] - reject_bands[:, 0])

            # subtract reject_band_amplitude
            for reject_band in reject_bands:
                reject_band_amplitude = self.amplitude(reject_band)
                net_amplitude = net_amplitude - (
                    reject_band_amplitude / reject_bands_total_bandwidth
                )

            # negative signal shouldn't be kept, because it means reject was stronger than signal. Zero it:
            net_amplitude = [max(0, s) for s in net_amplitude]

        return net_amplitude

    def to_image(
        self, shape=None, channels=1, colormap=None, invert=False, return_type="pil"
    ):
        """Create an image from spectrogram (array, tensor, or PIL.Image)

        Linearly rescales values in the spectrogram from
        self.decibel_limits to [0,255] (PIL.Image) or [0,1] (array/tensor)

        Default of self.decibel_limits on load is [-100, -20], so, e.g.,
        -20 db is loudest -> black, -100 db is quietest -> white

        Args:
            shape: tuple of output dimensions as (height, width)
                - if None, retains original shape of self.spectrogram
            channels: eg 3 for rgb, 1 for greyscale
                - must be 3 to use colormap
            colormap:
                if None, greyscale spectrogram is generated
                Can be any matplotlib colormap name such as 'jet'
            return_type: type of returned object
                - 'pil': PIL.Image
                - 'np': numpy.ndarray
                - 'torch': torch.tensor
        Returns:
            Image/array with type depending on `return_type`:
            - PIL.Image with c channels and shape w,h given by `shape`
                and values in [0,255]
            - np.ndarray with shape [c,h,w] and values in [0,1]
            - or torch.tensor with shape [c,h,w] and values in [0,1]
        """
        from skimage.transform import resize as skresize

        assert return_type in [
            "pil",
            "np",
            "torch",
        ], f"Arg `return_type` must be one of 'pil', 'np', 'torch'. Got {return_type}."
        if colormap is not None:
            # it doesn't make sense to use a colormap with #channels != 3
            assert (
                channels == 3
            ), f"Channels must be 3 to use colormap. Specified {channels}"

        # rescale spec_range to [1, 0]
        # note the low values represent silence, so a silent img would be black
        # if plotted directly from these values.
        array = linear_scale(
            self.spectrogram, in_range=self.decibel_limits, out_range=(0, 1)
        )
        # flip so that frequency increases from bottom to top
        array = array[::-1, :]

        # invert if desired
        if invert:
            array = 1 - array

        # apply colormaps
        if colormap is not None:  # apply a colormap to get RGB channels
            cm = get_cmap(colormap)
            array = cm(array)

        # resize and change channel dims
        if shape is None:
            shape = np.shape(array)
        out_shape = [shape[0], shape[1], channels]
        array = skresize(array, out_shape)

        if return_type == "pil":  # expected shape of input is [h,w,c]
            from PIL import Image

            # use correct type for img, and scale from 0-1 to 0-255
            array = np.uint8(array * 255)
            if array.shape[-1] == 1:
                # PIL doesnt like [x,y,1] shape, wants [x,y] instead
                array = array[:, :, 0]
            image = Image.fromarray(array)

        elif return_type == "np":  # shape should be c,h,w
            image = array.transpose(2, 0, 1)

        elif return_type == "torch":  # shape should be c,h,w
            import torch

            image = torch.Tensor(array.transpose(2, 0, 1))

        return image


class MelSpectrogram(Spectrogram):
    """Immutable mel-spectrogram container

    A mel spectrogram is a spectrogram with pseudo-logarithmically spaced
    frequency bins (see literature) rather than linearly spaced bins.

    See Spectrogram class an Librosa's melspectrogram for detailed documentation.

    NOTE: Here we rely on scipy's spectrogram function (via Spectrogram)
    rather than on librosa's _spectrogram or melspectrogram, because the
    amplitude of librosa's spectrograms do not match expectations. We only
    use the mel frequency bank from Librosa.
    """

    def __repr__(self):
        return f"<MelSpectrogram(spectrogram={self.spectrogram.shape}, frequencies={self.frequencies.shape}, times={self.times.shape})>"

    @classmethod
    def from_audio(
        cls,
        audio,
        n_mels=64,
        window_samples=512,
        overlap_samples=256,
        decibel_limits=(-100, -20),
        htk=False,
        norm="slaney",
        window_type="hann",
        dB_scale=True,
        scaling="spectrum",
    ):
        """Create a MelSpectrogram object from an Audio object

        First creates a spectrogram and a mel-frequency filter bank,
        then computes the dot product of the filter bank with the spectrogram.

        The kwargs for the mel frequency bank are documented at:
        - https://librosa.org/doc/latest/generated/librosa.feature.melspectrogram.html#librosa.feature.melspectrogram
        - https://librosa.org/doc/latest/generated/librosa.filters.mel.html?librosa.filters.mel

        Args:
            n_mels: Number of mel bands to generate [default: 128]
                Note: n_mels should be chosen for compatibility with the
                Spectrogram parameter `window_samples`. Choosing a value
                `> ~ window_samples/10` will result in zero-valued rows while
                small values blend rows from the original spectrogram.
            window_type: The windowing function to use [default: "hann"]
            window_samples: n samples per window [default: 512]
            overlap_samples: n samples shared by consecutive windows [default: 256]
            htk: use HTK mel-filter bank instead of Slaney, see Librosa docs [default: False]
            norm='slanley': mel filter bank normalization, see Librosa docs
            dB_scale=True: If True, rescales values to decibels, x=10*log10(x)
                - if dB_scale is False, decibel_limits is ignored
            scaling="spectrum": see scipy.signal.spectrogram docs for description of scaling parameter

        Returns:
            opensoundscape.spectrogram.MelSpectrogram object
        """

        if not isinstance(audio, Audio):
            raise TypeError("Class method expects Audio class as input")

        # Generate a linear-frequency spectrogram
        # with raw stft values rather than decibels
        linear_spec = Spectrogram.from_audio(
            audio,
            window_type=window_type,
            window_samples=window_samples,
            overlap_samples=overlap_samples,
            dB_scale=False,
            scaling=scaling,
        )

        # choose n_fft to ensure filterbank.size[1]==spectrogram.size[0]
        n_fft = int(linear_spec.spectrogram.shape[0] - 1) * 2
        # Construct mel filter bank
        fb = librosa.filters.mel(
            audio.sample_rate, n_fft, n_mels=n_mels, norm=norm, htk=htk
        )
        # normalize filter bank: rows should sum to 1 #TODO: is this correct?
        fb_constant = np.sum(fb, 1).mean()
        fb = fb / fb_constant

        # Apply filter bank to spectrogram with matrix multiplication
        melspectrogram = np.dot(fb, linear_spec.spectrogram)

        if dB_scale:  # convert to decibels
            melspectrogram = 10 * np.log10(
                melspectrogram,
                where=melspectrogram > 0,
                out=np.full(melspectrogram.shape, -np.inf),
            )

            # limit the decibel range (-100 to -20 dB by default)
            # values below lower limit set to lower limit,
            # values above upper limit set to uper limit
            min_db, max_db = decibel_limits
            melspectrogram[melspectrogram > max_db] = max_db
            melspectrogram[melspectrogram < min_db] = min_db

        # Calculate mel frequency bins
        frequencies = librosa.filters.mel_frequencies(
            n_mels=n_mels, fmin=0, fmax=audio.sample_rate / 2, htk=htk
        )

        return cls(
            melspectrogram,
            frequencies,
            linear_spec.times,
            decibel_limits,
            window_samples=window_samples,
            overlap_samples=overlap_samples,
            window_type=window_type,
            audio_sample_rate=audio.sample_rate,
            scaling=scaling,
        )

    def plot(self, inline=True, fname=None, show_colorbar=False):
        """Plot the mel spectrogram with matplotlib.pyplot

        We can't use pcolormesh because it will smash pixels to achieve
        a linear y-axis, rather than preserving the mel scale.

        Args:
            inline=True:
            fname=None: specify a string path to save the plot to (ending in .png/.pdf)
            show_colorbar: include image legend colorbar from pyplot
        """
        from matplotlib import pyplot as plt
        import matplotlib.colors

        color_norm = matplotlib.colors.Normalize(
            vmin=self.decibel_limits[0], vmax=self.decibel_limits[1]
        )

        plt.imshow(self.spectrogram[::-1], cmap="Greys", norm=color_norm)

        # pick values to show on time and frequency axes
        yvals = self.frequencies.round(-2).astype(int)
        xvals = self.times.round(2)
        y_idx = [int(ti) for ti in np.linspace(0, len(yvals), 8)]
        y_idx[-1] -= 1
        plt.yticks(len(yvals) - np.array(y_idx), yvals[y_idx])
        x_idx = [int(ti) for ti in np.linspace(0, len(xvals), 6)]
        x_idx[-1] -= 1
        plt.xticks(x_idx, xvals[x_idx])

        # add axes labels
        plt.ylabel("frequency (Hz): mel scale")
        plt.xlabel("time (sec)")

        if show_colorbar:
            plt.colorbar()

        # if fname is not None, save to file path fname
        if fname:
            plt.savefig(fname)

        # if not saving to file, check if a matplotlib backend is available
        if inline:
            import os

            if os.environ.get("MPLBACKEND") is None:
                warnings.warn("MPLBACKEND is 'None' in os.environ. Skipping plot.")
            else:
                plt.show()
