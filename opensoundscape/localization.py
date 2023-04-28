"""Tools for localizing audio events from synchronized recording arrays"""
import warnings
import numpy as np
import pandas as pd
from opensoundscape.audio import Audio
from scipy.signal import correlate, correlation_lags

import opensoundscape.signal_processing as sp
from opensoundscape import audio

# define defaults for physical constants
SPEED_OF_SOUND = 343  # default value in meters per second


class InsufficientReceiversError(Exception):
    """raised when there are not enough receivers to localize an event"""

    pass


class SpatialEvent:
    """
    Class that estimates the position of a single sound event

    Uses reciever positions and time-of-arrival of sounds to estimate
    soud source position
    """

    def __init__(
        self,
        receiver_files,
        receiver_positions,
        start_time=0,
        duration=None,
        class_name=None,
        bandpass_range=None,
        cc_threshold=None,
        max_delay=None,
    ):
        """initialize SpatialEvent

        Args:
            receiver_files: list of audio files, one for each reciever
            receiver_positions: list of [x,y] or [x,y,z] cartesian position of each receiver in meters
            start_time: start position of detection relative to start of audio file, for cross correlation
            duration: duration of audio segment to use for cross-correlation
            class_name=None: (str) name of detection's class
            tdoas=None: optionally specify relative time difference of arrival of event at each receiver, in
                seconds. If not None, should be list of same length as receiver_files and receiver_positions
            bandpass_range: [low,high] frequency for audio bandpass before cross-correlation
                default [None]: does not perform audio bandpass before cross-correlation
            cc_threshold: float, default=None. During localization from time delays, discard time delays and
                associated positions if max cross correlation value was lower than this threshold.
                default: None uses all delays and positions regardless of max cc value
            max_delay: maximum time delay (in seconds) to consider for cross correlation
                (see `opensoundscape.signal_processing.tdoa`)

        Methods:
            estimate_delays:
                use generalized cross correlation to find time delays of arrival
                of the event at each receiver
            estimate_location:
                perform tdoa based position estimation
                - calls estimate_delays() if needed
            calculate_distance_residuals:
                compute residuals (descrepancies) between tdoa and estimated position
            calculate_residual_rms:
                compute the root mean square value of the tdoa distance residuals

        """
        self.receiver_files = receiver_files
        self.receiver_positions = np.array(receiver_positions)
        self.start_time = start_time
        self.duration = duration
        self.class_name = class_name
        self.bandpass_range = bandpass_range
        self.cc_threshold = cc_threshold
        self.max_delay = max_delay

        # initialize attributes to store values calculated by methods
        self.tdoas = None  # time delay at each receiver
        self.cc_maxs = None  # max of cross correlation for each time delay
        self.position_estimate = None  # cartesian position estimate in meters
        self.residual_rms = None

        # could implement this later:
        # hidden attributes store estimates and error metrics
        # from gillette and soundfinder localization algorithms
        # self._gillette_position_estimate = None
        # self._gillette_error = None
        # self._soundfinder_position_estimate = None
        # self._soundfinder_pseudorange_error = None

    def estimate_location(
        self,
        algorithm="gillette",
        cc_threshold=None,
        min_n_receivers=3,
        speed_of_sound=SPEED_OF_SOUND,
    ):
        """
        estimate spatial location of this event

        uses self.tdoas and self.receiver_positions to estimate event location

        Note: if self.tdoas or self.receiver_positions is None, first calls
        self.estimate_delays() to estimate these values

        Localization is performed in 2d or 3d according to the dimensions of
        self.receiver_positions (x,y) or (x,y,z)

        Args:
            algorithm: 'gillette' or 'soundfinder', see localization.localize()
            cc_threshold: see SpatialEvent documentation
            min_n_receivers: if number of receivers with cross correlation exceeding
                the threshold is fewer than this, raises InsufficientReceiversError
                instead of estimating a spatial position

        Returns:
            position estimate as cartesian coordinates (x,y) or (x,y,z) (units: meters)

        Raises:
            InsufficientReceiversError if the number of receivers with cross correlation
                maximums exceeding `cc_threshold` is less than `min_n_receivers`

        Effects:
            sets the value of self.position_estimate to the same value as the returned position
        """
        if cc_threshold is None:
            cc_threshold = self.cc_threshold

        # perform generalized cross correlation to estimate time delays
        # (unless values are already stored in attributes)
        if self.tdoas is None or self.cc_maxs is None:
            self.estimate_delays()

        # filter by cross correlation threshold, removing time delays + positions
        # if cross correlation did not exceed a minimum value
        # (low max cross correlation values indicate low confidence that the time
        # delay truly represents two recordings of the same sound event)
        tdoas = self.tdoas
        positions = self.receiver_positions
        if cc_threshold is not None:
            tdoas = tdoas[self.cc_maxs > cc_threshold]
            positions = positions[self.cc_maxs > cc_threshold]

        # assert there are enough receivers remaining to localize the event
        if len(tdoas) < min_n_receivers:
            raise InsufficientReceiversError(
                f"Number of tdoas exceeding cc threshold ({len(tdoas)} was fewer "
                f"than min_n_receivers ({min_n_receivers})"
            )

        # estimate location from receiver positions and relative time of arrival
        # TODO: enable returning error estimates
        self.position_estimate = localize(
            receiver_positions=positions,
            tdoas=tdoas,
            algorithm=algorithm,
            speed_of_sound=speed_of_sound,
        )

        # calculate rms distance residual, descrepancy between tdoas and estimated position
        self.residual_rms = self.calculate_residual_rms(speed_of_sound=speed_of_sound)

        return self.position_estimate

    def estimate_delays(self, bandpass_range=None, cc_filter="phat", max_delay=None):
        """estimate time delay of event relative to receiver_files[0] with gcc

        Performs Generalized Cross Correlation of each file against the first,
            extracting the segment of audio of length self.duration at self.start_time

        Assumes audio files are synchronized such that they start at the same time

        Args:
            bandpass_range: bandpass audio to [low, high] frequencies in Hz before
                cross correlation; if None, defaults to self.bandpass_range
            cc_filter: filter for generalized cross correlation, see
                opensoundscape.signal_processing.gcc()
            max_delay: only consider values in +/- this range (seconds) for possible delay
                (see opensoundscape.signal_processing.tdoa())
                - default None allows any delay time

        Returns:
            list of time delays, list of max cross correlation values

            each list is the same length as self.receiver_files, and each
            value corresponds to the cross correlation of one file relative
            to the first file (self.receiver_files[0])

        Effects:
            sets self.tdoas and self.cc_maxs with the same values as those returned
        """
        start, dur = self.start_time, self.duration
        audio1 = Audio.from_file(self.receiver_files[0], offset=start, duration=dur)

        # bandpass once now to avoid repeating operation for each receiver
        if bandpass_range is not None:
            audio1 = audio1.bandpass(bandpass_range[0], bandpass_range[1], order=9)

        # estimate time difference of arrival (tdoa) for each file relative to the first
        # skip the first because we don't need to cross correlate a file with itself
        tdoas = [0]  # first file's delay to itself is zero
        cc_maxs = [1]
        for file in self.receiver_files[1:]:
            audio2 = Audio.from_file(file, offset=start, duration=dur)
            tdoa, cc_max = audio.estimate_delay(
                audio=audio2,
                reference_audio=audio1,
                bandpass_range=bandpass_range,
                cc_filter=cc_filter,
                return_cc_max=True,
                max_delay=max_delay,
                skip_ref_bandpass=True,
            )
            tdoas.append(tdoa)
            cc_maxs.append(cc_max)

        self.tdoas = np.array(tdoas)
        self.cc_maxs = np.array(cc_maxs)

        return self.tdoas, self.cc_maxs

    def calculate_distance_residuals(self, speed_of_sound=SPEED_OF_SOUND):
        """calculate distance residuals for each receiver from tdoa localization

        The residual represents the discrepancy between (difference in distance
        of each reciever to estimated position) and (observed tdoa), and has
        units of meters.

        Args:
            speed_of_sound: speed of sound in m/s

        Returns:
            array of residuals (units are meters), one for each receiver

        Effects:
            stores the returned residuals as self.distance_residuals
        """
        if self.tdoas is None or self.position_estimate is None:
            warnings.warn(
                "missing self.tdoas or self.position_estimate, "
                "returning None for distance_residuals"
            )
            return None

        # store the calcualted tdoas as an attribute
        self.distance_residuals = calculate_tdoa_residuals(
            receiver_positions=self.receiver_positions,
            tdoas=self.tdoas,
            position_estimate=self.position_estimate,
            speed_of_sound=speed_of_sound,
        )

        # return the same residuals
        # TODO I think this is bad programming because the array could be modified
        # same issue with other methods of this class
        return self.distance_residuals

    def calculate_residual_rms(self, speed_of_sound=SPEED_OF_SOUND):
        """calculate the root mean square distance residual from tdoa localization

        Args:
            speed_of_sound: speed of sound in meters per second

        Returns:
            root mean square value of residuals, in meters
            - returns None if self.tdoas or self.position_estimate
                are None

        See also: `SpatialEvent.calculate_distance_residuals()`
        """
        if self.tdoas is None or self.position_estimate is None:
            warnings.warn(
                "missing `self.tdoas` or `self.position_estimate`, "
                "returning None for residual rms"
            )
            return None

        # calculate the residual distance for each reciever
        # this represents the discrepancy between (difference in distance
        # of each reciever to estimated position) and (observed tdoa)
        residuals = self.calculate_distance_residuals(speed_of_sound)
        return np.sqrt(np.mean(residuals**2))


class SynchronizedRecorderArray:
    """
    Class with utilities for localizing sound events from array of recorders

    Methods
    -------
    localize_detections()
        Attempt to localize a sound event for each detection of each class.
        First, creates candidate events with:
        create_candidate_events()
            Create SpatialEvent objects for all simultaneous, spatially clustered detections of a class

        Then, attempts to localize each candidate event via time delay of arrival information:
        For each candidate event:
            - calculate relative time of arrival with generalized cross correlation (event.estimate_delays())
            - if enough cross correlation values exceed a threshold, attempt to localize the event
                using the time delays and spatial positions of each receiver with event.estimate_location()
            - if the residual distance rms value is below a cutoff threshold, consider the event
                to be successfully localized
    """

    def __init__(
        self,
        file_coords,
    ):
        """
        Args:
            file_coords : pandas.DataFrame
                DataFrame with index filepath, and columns for x, y, (z) positions of reciever that
                recorded the audio file, in meters.
                Third coordinate is optional. Localization algorithms are in 2d if columns are (x,y) and
                3d if columns are (x,y,z). When running .localize_detections() or .create_candidate_events(),
                Each audio file in `detections` must have a corresponding
                row in `file_coords` specifiying the position of the reciever that recorded the file.
        """
        self.file_coords = file_coords

    def localize_detections(
        self,
        detections,
        max_receiver_dist,
        min_n_receivers=3,
        localization_algorithm="gillette",
        cc_threshold=0,
        cc_filter="phat",
        max_delay=None,
        bandpass_ranges=None,
        residual_threshold=np.inf,
    ):
        """
        Attempt to localize positions for all detections

        Algorithm
        ----------
        The user provides a table of class detections from each recorder with timestamps.
        The object's self.file_coords dataframe contains a table listing the spatial location of the
        recorder for each unique audio file in the table of detections. The audio recordings must
        be synchronized such that timestamps from each recording correspond to the exact same real-world time.

        Localization of sound events proceeds in four steps:

        1. Grouping of detections into candidate events (self.create_candidate_events()):

            Simultaneous and spatially clustered detections of a class are selected as targets
            for localization of a single real-world sound event.

            For each detection of a species, the grouping algorithm treats the reciever with the detection
            as a "reference receiver", then selects all detections of the species at the same time and
            within `max_receiver_dist` of the reference reciever (the "surrounding detections").
            This selected group of simulatneous, spatially-clustered detections of a class beomes one
            "candidate event" for subsequent localization.

            If the number of recorders in the candidate event is fewer than `min_n_receivers`, the
            candidate event is discarded.

            This step creates a highly redundant set of candidate events to localize, because each detection
            is treated separately with its recorder as the 'reference recorder'. Thus, the localized
            events created by this algorithm may contain multiple instances representing
            the same real-world sound event.


        2. Estimate time delays with cross correlation:

            For each candidate event, the time delay between the reference reciever's detection and the
            surrounding recorders' detections is estimated through generalized cross correlation.

            If bandpass_ranges are provided, cross correlation is performed on audio that has been
            bandpassed to class-specific low and high frequencies.

            If the max value of the cross correlation is below `cc_threshold`, the corresponding time delay
            is discarded and not used during localization. This provides a way of filtering out
            undesired time delays that do not correspond to two recordings of the same sound event.

            If the number of estimated time delays in the candidate event is fewer than `min_n_receivers`
            after filtering by cross correlation threshold, the candidate event is discarded.

        3. Estimate positions

            The position of the event is estimated based on the positions and time delays of
            each detection.

            Position estimation from the positions and time delays at a set of receivers is performed
            using one of two algorithms, described in `localization_algorithm` below.

        4. Filter by spatial residual error

            The residual errors represent descrepencies between (a) time of arrival of
            the event at a reciever and (b) distance from reciever to estimated position.

            Estimated positions are discarded if the root mean squared spatial residual is
            greater than `residual_rms_threshold`


        Args:
            detections: a dictionary of detections, with multi-index (file,start_time,end_time), and
                one column per class with 0/1 values for non-detection/detection
                The times in the index imply the same real world time across all files: eg 0 seconds assumes
                that the audio files all started at the same time, not on different dates/times
            max_receiver_dist : float (meters)
                Radius around a recorder in which to use other recorders for localizing an event.
                Simultaneous detections at receivers within this distance (meters)
                of a receiver with a detection will be used to attempt to localize the event.
            min_n_receivers : int
                Minimum number of receivers that must detect an event for it to be localized
                [default: 3]
            localization_algorithm : str, optional
                algorithm to use for estimating the position of a sound event from the positions and
                time delays of a set of detections. [Default: 'gillette']
                Options:
                    - 'gillette': linear closed-form algorithm of Gillette and Silverman 2008 [1]
                    - 'soundfinder': GPS position algorithm of Wilson et al. 2014 [2]
            cc_threshold : float, optional
                Threshold for cross correlation: if the max value of the cross correlation is below
                this value, the corresponding time delay is discarded and not used during localization.
                Default of 0 does not discard any delays.
            cc_filter : str, optional
                Filter to use for generalized cross correlation. See signalprocessing.gcc function for options.
                Default is "phat".
            max_delay : float, optional
                Maximum absolute value of time delay estimated during cross correlation of two signals
                For instance, 0.2 means that cross correlation will be maximized in the range of
                delays between -0.2 to 0.2 seconds.
                Default: None does not restrict the range, finding delay that maximizes cross correlation
            bandpass_ranges : dict, optional
                Dictionary of form {"class name": [low_f, high_f]} for audio bandpass filtering during
                cross correlation. [Default: None] does not bandpass audio. Bandpassing audio to the
                frequency range of the relevant sound is recommended for best cross correlation results.
            residual_threshold: discard localized events if the root mean squared residual exceeds this value
                (distance in meters) [default: np.inf does not filter out any events by residual]

        Returns:
            2 lists: list of localized events, list of un-localized events
            events are of class SpatialEvent

        [1] M. D. Gillette and H. F. Silverman, "A Linear Closed-Form Algorithm for Source Localization
        From Time-Differences of Arrival," IEEE Signal Processing Letters

        [2]  Wilson, David R., Matthew Battiston, John Brzustowski, and Daniel J. Mennill.
        “Sound Finder: A New Software Approach for Localizing Animals Recorded with a Microphone Array.”
        Bioacoustics 23, no. 2 (May 4, 2014): 99–112. https://doi.org/10.1080/09524622.2013.827588.
        """

        # check that all files have coordinates in file_coords
        if len(self.check_files_missing_coordinates(detections)) > 0:
            raise UserWarning(
                "WARNING: Not all audio files have corresponding coordinates in self.file_coords."
                "Check file_coords.index contains each file in detections.index. "
                "Use self.check_files_missing_coordinates() for list of files. "
            )
        # check that bandpass_ranges have been set for all classes
        if bandpass_ranges is not None:
            if set(bandpass_ranges.keys()) != set(detections.columns):
                warnings.warn(
                    "WARNING: Not all classes have corresponding bandpass ranges. "
                    "Default behavior will be to not bandpass before cross-correlation for "
                    "classes that do not have a corresponding bandpass range."
                )  # TODO support one bandpass range for all classes

        # initialize list to store events that successfully localize
        localized_events = []
        unlocalized_events = []

        # create list of SpatialEvent objects to attempt to localize
        # creates events for every detection, adding nearby detections
        # to assist in localization via time delay of arrival
        candidate_events = self.create_candidate_events(
            detections,
            min_n_receivers,
            max_receiver_dist,
        )

        # attempt to localize each event
        for event in candidate_events:
            # choose bandpass range based on this event's detected class
            if bandpass_ranges is not None:
                bandpass_range = bandpass_ranges[event.class_name]
            else:
                bandpass_range = None

            # perform gcc to estiamte relative time of arrival at each receiver
            # relative to the first in the list (reference receiver)
            event.estimate_delays(
                bandpass_range=bandpass_range,
                cc_filter=cc_filter,
                max_delay=max_delay,
            )

            # estimate positions of sound event using time delays and receiver positions
            try:
                event.estimate_location(
                    algorithm=localization_algorithm,
                    cc_threshold=cc_threshold,
                    min_n_receivers=min_n_receivers,
                    speed_of_sound=SPEED_OF_SOUND,
                )
            except InsufficientReceiversError:
                # this occurs if not enough receivers had high enough cross correlation scores
                # to continue with localization (<min_n_receivers)
                unlocalized_events.append(event)
                continue

            # event.residual_rms is computed at the end of event.estimate_location
            # and represents descrepency (in meters) between tdoas and estimated position
            # check if residuals are small enough that we consider this a good position estimate
            # TODO: use max instead of mean?
            if event.residual_rms < residual_threshold:
                localized_events.append(event)
            else:
                unlocalized_events.append(event)

        # unlocalized events include those with too few receivers (after cc threshold)
        # and those with too large of a spatial residual rms
        return localized_events, unlocalized_events

    def check_files_missing_coordinates(self, detections):
        files_missing_coordinates = []
        files = list(detections.reset_index()["file"].unique())
        for file in files:
            if str(file) not in self.file_coords.index:
                files_missing_coordinates.append(file)
        return files_missing_coordinates

    def create_candidate_events(
        self,
        detections,
        min_n_receivers,
        max_receiver_dist,
    ):
        """
        Takes the detections dictionary and groups detections that are within `max_receiver_dist` of each other.
        args:
            detections: a dictionary of detections, with multi-index (file,start_time,end_time), and
                one column per class with 0/1 values for non-detection/detection
                The times in the index imply the same real world time across all files: eg 0 seconds assumes
                that the audio files all started at the same time, not on different dates/times
            min_n_receivers: if fewer nearby receivers have a simultaneous detection, do not create candidate event
            `max_receiver_dist`: the maximum distance between recorders to consider a detection as a single event
        returns:
            a list of SpatialEvent objects to attempt to localize
        """
        # pre-generate a dictionary listing all close files for each audio file
        # dictionary will have a key for each audio file, and value listing all other receivers
        # within max_receiver_dist of that receiver
        #
        # eg {ARU_0.mp3: [ARU_1.mp3, ARU_2.mp3...], ARU_1... }
        nearby_files_dict = self.make_nearby_files_dict(max_receiver_dist)

        # generate SpatialEvents for each detection, if enough nearby
        # receivers also had a detection at the same time
        # each SpatialEvent object contains the time and class name of a
        # detected event, a set of receivers' audio files, and receiver positions
        # and represents a single sound event that we will try to localize
        #
        # events will be redundant because each reciever with detection potentially
        # results in its own event containing nearby detections
        candidate_events = []  # list of SpatialEvents to try to localize

        # iterate through all classes in detections (0/1) dataframe
        # with index (file,start_time,end_time), column for each class
        for cls_i in detections.columns:

            # select one column: contains 0/1 for each file and clip time period
            # (index: (file,start_time,end_time), values: 0 or 1) for a single class
            det_cls = detections[[cls_i]]

            # filter detection dataframe to select detections of this class
            det_cls = det_cls[det_cls[cls_i] > 0]

            # iterate through each clip start time in the df of detections
            # note: all clips with same start_time are assumed to start at the same real-world time!
            # eg, should not be two recordings from different dates or times
            # TODO: maybe use datetime objects instead of just a time like 0 seconds?
            for time_i, dets_at_time_i in det_cls.groupby(level=1):
                # list all files with detections of this class at the same time
                files_w_dets = dets_at_time_i.reset_index()["file"].unique()

                # for each file with detection of this class at this time,
                # check how many nearby recorders have a detection
                # at the same time. If there are enough, make a SpatialEvent
                # containing the spatial cluster of detections. The event
                # will be added to the list of candidate_events to localize
                for ref_file in files_w_dets:
                    # check how many other detections are close enough to be detections of
                    # the same sound event
                    # first, use pre-created dictionary of nearby receivers for each audio file
                    close_receivers = nearby_files_dict[ref_file]
                    # then, subset files with detections to those that are nearby
                    close_det_files = [f for f in files_w_dets if f in close_receivers]

                    # if enough receivers, create a SpatialEvent using this set of receivers
                    # +1 to count the reference receiver
                    if len(close_det_files) + 1 >= min_n_receivers:
                        receiver_files = [ref_file] + close_det_files
                        receiver_positions = [
                            self.file_coords.loc[r] for r in receiver_files
                        ]
                        # find the clip end time
                        clip = dets_at_time_i.loc[ref_file, time_i, :]
                        clip_end = clip.reset_index()["end_time"].values[0]

                        # create a SpatialEvent for this cluster of simultaneous detections
                        candidate_events.append(
                            SpatialEvent(
                                receiver_files=receiver_files,
                                receiver_positions=receiver_positions,
                                start_time=time_i,
                                duration=clip_end - time_i,
                                class_name=cls_i,
                            )
                        )

        return candidate_events

    def make_nearby_files_dict(self, r_max):
        """create dictinoary listing nearby files for each file

        pre-generate a dictionary listing all close files for each audio file
        dictionary will have a key for each audio file, and value listing all other receivers
        within r_max of that receiver

        eg {ARU_0.mp3: [ARU_1.mp3, ARU_2.mp3...], ARU_1... }

        Note: could manually create this dictionary to only list _simulataneous_ nearby
        files if the detection dataframe contains files from different times

        The returned dictionary is used in create_candidate_events as a look-up table for
            recordings nearby a detection in any given file

        Args:
            r_max: maximum distance from each recorder in which to include other
                recorders in the list of 'nearby recorders', in meters

        Returns:
            dictionary with keys for each file and values = list of nearby recordings
        """
        aru_files = self.file_coords.index.values
        nearby_files_dict = dict()
        for aru in aru_files:  # make an entry in the dictionary for each file
            reference_receiver = self.file_coords.loc[aru]  # position of receiver
            other_receivers = self.file_coords.drop([aru])
            distances = np.array(other_receivers) - np.array(reference_receiver)
            euclid_distances = [np.linalg.norm(d) for d in distances]

            # boolean mask for whether recorder is close enough
            mask = [r <= r_max for r in euclid_distances]
            nearby_files_dict[aru] = list(other_receivers[mask].index)

        return nearby_files_dict


def calc_speed_of_sound(temperature=20):
    """
    Calculate speed of sound in air, in meters per second

    Calculate speed of sound for a given temperature
    in Celsius (Humidity has a negligible
    effect on speed of sound and so this functionality
    is not implemented)

    Args:
        temperature: ambient air temperature in Celsius

    Returns:
        the speed of sound in air in meters per second
    """
    return 331.3 * np.sqrt(1 + float(temperature) / 273.15)


def lorentz_ip(u, v=None):
    """
    Compute Lorentz inner product of two vectors

    For vectors `u` and `v`, the
    Lorentz inner product for 3-dimensional case is defined as

        u[0]*v[0] + u[1]*v[1] + u[2]*v[2] - u[3]*v[3]

    Or, for 2-dimensional case as

        u[0]*v[0] + u[1]*v[1] - u[2]*v[2]

    Args:
        u: vector with shape either (3,) or (4,)
        v: vector with same shape as x1; if None (default), sets v = u

    Returns:
        float: value of Lorentz IP"""
    if v is None:
        v = u

    if len(u) == 3 and len(v) == 3:
        c = [1, 1, -1]
        return sum([u[i] * v[i] * c[i] for i in range(len(u))])
    elif len(u) == 4 and len(v) == 4:
        c = [1, 1, 1, -1]
        return sum([u[i] * v[i] * c[i] for i in range(len(u))])

    return ValueError(f"length of x should be 3 or 4, was{len(u)}")


def travel_time(source, receiver, speed_of_sound):
    """
    Calculate time required for sound to travel from a souce to a receiver

    Args:
        source: cartesian position [x,y] or [x,y,z] of sound source, in meters
        receiver: cartesian position [x,y] or [x,y,z] of sound receiver, in meters
        speed_of_sound: speed of sound in m/s

    Returns:
        time in seconds for sound to travel from source to receiver
    """
    distance = np.linalg.norm(np.array(source) - np.array(receiver))
    return distance / speed_of_sound


def localize(receiver_positions, tdoas, algorithm, speed_of_sound=SPEED_OF_SOUND):
    """
    Perform TDOA localization on a sound event.
    Args:
        receiver_positions: a list of [x,y,z] positions for each receiver
            Positions should be in meters, e.g., the UTM coordinate system.
        tdoas: a list of TDOA times (onset times) for each recorder
            The times should be in seconds.
        speed_of_sound: speed of sound in m/s
        algorithm: the algorithm to use for localization
            Options: 'soundfinder', 'gillette'
    Returns:
        The estimated source position in meters.
    """
    if algorithm == "soundfinder":
        estimate = soundfinder_localize(receiver_positions, tdoas, speed_of_sound)
    elif algorithm == "gillette":
        estimate = gillette_localize(receiver_positions, tdoas, speed_of_sound)
    else:
        raise ValueError(
            f"Unknown algorithm: {algorithm}. Implemented for 'soundfinder' and 'gillette'"
        )
    return estimate


def soundfinder_localize(
    receiver_positions,
    arrival_times,
    speed_of_sound=SPEED_OF_SOUND,
    invert_alg="gps",  # options: 'gps'
    center=True,  # True for original Sound Finder behavior
    pseudo=True,  # False for original Sound Finder
):

    """
    Use the soundfinder algorithm to perform TDOA localization on a sound event
    Localize a sound event given relative arrival times at multiple receivers.
    This function implements a localization algorithm from the
    equations described in [1]. Localization can be performed in a global coordinate
    system in meters (i.e., UTM), or relative to recorder positions
    in meters.

    This implementation follows [2] with corresponding variable names.

    Args:
        receiver_positions: a list of [x,y,z] positions for each receiver
          Positions should be in meters, e.g., the UTM coordinate system.
        arrival_times: a list of TDOA times (onset times) for each recorder
          The times should be in seconds.
        speed of sound: speed of sound in m/s
        invert_alg: what inversion algorithm to use (only 'gps' is implemented)
        center: whether to center recorders before computing localization
          result. Computes localization relative to centered plot, then
          translates solution back to original recorder locations.
          (For behavior of original Sound Finder, use True)
        pseudo: whether to use the pseudorange error (True) or
          sum of squares discrepancy (False) to pick the solution to return
          (For behavior of original Sound Finder, use False. However,
          in initial tests, pseudorange error appears to perform better.)
    Returns:
        The solution (x,y,z) in meters.

    [1]  Wilson, David R., Matthew Battiston, John Brzustowski, and Daniel J. Mennill.
    “Sound Finder: A New Software Approach for Localizing Animals Recorded with a Microphone Array.”
    Bioacoustics 23, no. 2 (May 4, 2014): 99–112. https://doi.org/10.1080/09524622.2013.827588.

    [2] Global Positioning Systems handout, 2002
    http://web.archive.org/web/20110719232148/http://www.macalester.edu/~halverson/math36/GPS.pdf
    """

    # make sure our inputs follow consistent format
    receiver_positions = np.array(receiver_positions).astype("float64")
    arrival_times = np.array(arrival_times).astype("float64")

    # The number of dimensions in which to perform localization
    dim = receiver_positions.shape[1]
    assert dim in [2, 3], "localization only works in 2 or 3 dimensions"

    ##### Shift coordinate system to center receivers around origin #####
    if center:
        p_mean = np.mean(receiver_positions, 0)
        receiver_positions = np.array([p - p_mean for p in receiver_positions])

    ##### Compute B, a, and e #####
    # these correspond to [2] and are defined directly after equation 6

    # Find the pseudorange, rho, for each recorder
    # pseudorange (minus a constant) ~= distances from source to each receiver
    rho = np.array([arrival_times * (-1 * speed_of_sound)]).T

    # Concatenate the pseudorange column with x,y,z position to form matrix B
    B = np.concatenate((receiver_positions, rho), axis=1)

    # e is a vector of ones
    e = np.ones(receiver_positions.shape[0])

    # a is a 1/2 times a vector of squared Lorentz norms
    a = 0.5 * np.apply_along_axis(lorentz_ip, axis=1, arr=B)

    # choose between two algorithms to invert the matrix
    if invert_alg != "gps":
        raise NotImplementedError
        # original implementation of lstsq:
        # Compute B+ * a and B+ * e
        # using closest equivalent to R's solve(qr(B), e)
        # Bplus_e = np.linalg.lstsq(B, e, rcond=None)[0]
        # Bplus_a = np.linalg.lstsq(B, a, rcond=None)[0]

    else:  # invert_alg == 'gps' ('special' falls back to 'lstsq')
        ## Compute B+ = (B^T \* B)^(-1) \* B^T
        # B^T * B

        to_invert = np.matmul(B.T, B)

        try:
            inverted = np.linalg.inv(to_invert)

        except np.linalg.LinAlgError as err:
            # for 'gps' algorithm, simply fail
            # if invert_alg == "gps":
            warnings.warn("4")
            if "Singular matrix" in str(err):
                warnings.warn("5")
                warnings.warn(
                    "Singular matrix. Were recorders linear or on same plane? Exiting with NaN outputs",
                    UserWarning,
                )
                return [[np.nan]] * (dim)
            else:
                warnings.warn("6")
                raise

            # for 'special' algorithm: Fall back to lstsq algorithm
            # elif invert_alg == "special":  #
            #     warnings.warn("7")
            #     Bplus_e = np.linalg.lstsq(B, e, rcond=None)[0]
            #     Bplus_a = np.linalg.lstsq(B, a, rcond=None)[0]

        else:  # inversion of the matrix succeeded
            # B+ is inverse(B_transpose*B) * B_transpose
            # Compute B+ * a and B+ * e
            Bplus = np.matmul(inverted, B.T)
            Bplus_a = np.matmul(Bplus, a)
            Bplus_e = np.matmul(Bplus, e)

    ###### Solve quadratic equation for lambda #####

    # Compute coefficients
    cA = lorentz_ip(Bplus_e)
    cB = 2 * (lorentz_ip(Bplus_e, Bplus_a) - 1)
    cC = lorentz_ip(Bplus_a)

    # Compute discriminant
    disc = cB**2 - 4 * cA * cC
    # If discriminant is negative, set to zero to ensure
    # we get an answer, albeit not a very good one
    if disc < 0:
        disc = 0
        warnings.warn(
            "Discriminant negative--set to zero. Solution may be inaccurate. Inspect final value of output array",
            UserWarning,
        )

    # Compute options for lambda
    lamb = (-cB + np.array([-1, 1]) * np.sqrt(disc)) / (2 * cA)

    # Find solution u0 and solution u1
    ale0 = np.add(a, lamb[0] * e)
    u0 = np.matmul(Bplus, ale0)
    ale1 = np.add(a, lamb[1] * e)
    u1 = np.matmul(Bplus, ale1)

    # print('Solution 1: {}'.format(u0))
    # print('Solution 2: {}'.format(u1))

    ##### Return the better solution #####

    # Re-translate points
    if center:
        shift = np.append(p_mean, 0)  # 0 for b=error, which we don't need to shift
        u0 += shift
        u1 += shift

    # Select and return quadratic solution
    if pseudo:
        # Return the solution with the lower estimate of b, error in pseudorange
        # drop the estimate of b (error in pseudorange) from the return values,
        # returning just the position vector
        if abs(u0[-1]) <= abs(u1[-1]):
            return u0[0:-1]
        else:
            return u1[0:-1]

    else:
        # use the sum of squares discrepancy to choose the solution
        # This was the return method used in the original Sound Finder,
        # but it gives worse performance

        # Compute sum of squares discrepancies for each solution
        s0 = float(np.sum((np.matmul(B, u0) - np.add(a, lamb[0] * e)) ** 2))
        s1 = float(np.sum((np.matmul(B, u1) - np.add(a, lamb[1] * e)) ** 2))

        # Return the solution with lower sum of squares discrepancy
        # drop the final value, which is the estimate of b, error in the pseudorange,
        # returning just the position vector
        if s0 < s1:
            return u0[0:-1]
        else:
            return u1[0:-1]


def gillette_localize(receiver_positions, arrival_times, speed_of_sound=SPEED_OF_SOUND):
    """
    Uses the Gillette and Silverman [1] localization algorithm to localize a sound event from a set of TDOAs.
    Args:
        receiver_positions: a list of [x,y] or [x,y,z] positions for each receiver
            Positions should be in meters, e.g., the UTM coordinate system.
        arrival_times: a list of TDOA times (arrival times) for each receiver
            The times should be in seconds.
        speed_of_sound: speed of sound in m/s
    Returns:
        coords: a tuple of (x,y,z) coordinates of the sound source


    Algorithm from:
    [1] M. D. Gillette and H. F. Silverman, "A Linear Closed-Form Algorithm for Source Localization
    From Time-Differences of Arrival," IEEE Signal Processing Letters
    """

    # check that these delays are with reference to one receiver (the reference receiver).
    # We do this by checking that one of the arrival times is within float precision
    # of 0 (i.e. arrival at the reference)
    if not np.isclose(np.min(np.abs(arrival_times)), 0):
        raise ValueError(
            "Arrival times must be relative to a reference receiver. Therefore one arrival"
            " time must be 0 (corresponding to arrival at the reference receiver) None of your "
            "TDOAs are zero. Please check your arrival_times."
        )

    # make sure our inputs follow consistent format
    receiver_positions = np.array(receiver_positions).astype("float64")
    arrival_times = np.array(arrival_times).astype("float64")

    # The number of dimensions in which to perform localization
    dim = receiver_positions.shape[1]

    # find which is the reference receiver and reorder, so reference receiver is first
    ref_receiver = np.argmin(abs(arrival_times))
    ordered_receivers = np.roll(receiver_positions, -ref_receiver, axis=0)
    ordered_tdoas = np.roll(arrival_times, -ref_receiver, axis=0)

    # Gillette silverman solves Ax = w, where x is the solution vector, A is a matrix, and w is a vector
    # Matrix A according to Gillette and Silverman (2008)
    A = np.zeros((len(ordered_tdoas) - 1, dim + 1))
    for column in range(dim + 1):
        if column < dim:
            A[:, column] = ordered_receivers[0, column] - ordered_receivers[1:, column]
        elif column == dim:
            A[:, column] = ordered_tdoas[1:] * speed_of_sound

    # Vector w according to Gillette and Silverman (2008)
    # w = 1/2 (dm0^2 - xm^2 - ym^2 - zm^2 + x0^2 + y0^2 + z0^2)
    X02 = np.sum(ordered_receivers[0] ** 2)  # x0^2 + y0^2 + z0^2
    dmx = ordered_tdoas[1:] * speed_of_sound
    XM2 = np.sum(ordered_receivers**2, axis=1)[1:]

    vec_w = 0.5 * (dmx + X02 - XM2)

    answer = np.linalg.lstsq(A, vec_w.T, rcond=None)
    coords = answer[0][:dim]
    # pseudorange = answer[0][dim]
    # residuals = answer[1]

    return coords


def calculate_tdoa_residuals(
    receiver_positions, tdoas, position_estimate, speed_of_sound
):
    """
    Calculate the residual distances of the TDOA localization algorithm

    The residual represents the discrepancy between (difference in distance
    of each reciever to estimated position) and (observed tdoa), and has
    units of meters. Residuals are calculated as follows:

        expected = calculated time difference of arrival between reference and
            another receiver, based on the positions of the receivers and
            estimated event position
        observed = observed tdoas provided to localization algorithm

        residual time = expected - observed (in seconds)

        residual distance = speed of sound * residual time (in meters)

    Args:
        receiver_position: The list of coordinates (in m) of each receiver,
            as [x,y] for 2d or or [x,y,z] for 3d.
        tdoas: List of time delays of arival for the sound at each receiver,
            relative to the first receiver in the list (tdoas[0] should be 0)
        position_estimate: The estimated position of the sound, as (x,y) or (x,y,z) in meters
        speed_of_sound: The speed of sound in m/s

    Returns:
        np.array containing the residuals in units of meters, one per receiver
    """
    # ensure all are numpy arrays
    receiver_positions = np.array(receiver_positions)
    tdoas = np.array(tdoas)
    position_estimate = np.array(position_estimate)

    # Calculate the TDOA residuals

    # calculate time sound would take to travel from the estimated position
    # to each receiver (distance/speed=time)
    distances = [np.linalg.norm(r - position_estimate) for r in receiver_positions]
    travel_times = np.array(distances) / speed_of_sound

    # the expected time _difference_ of arrival for any receiver vs the
    # reference receiver is the difference in travel times from the
    # position estimate to each of the receivers compared to the first
    expected_tdoas = travel_times - travel_times[0]

    # the time residual is the difference between the observed tdoa values
    # and those expected according to the estimated position
    # first value will be 0 by definition
    time_residuals = expected_tdoas - tdoas

    # convert residuals from units of time (s) to distance (m) via speed of sound
    return time_residuals * speed_of_sound
