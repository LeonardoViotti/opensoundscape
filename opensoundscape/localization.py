"""Tools for localizing audio events from synchronized recording arrays"""
import warnings
import numpy as np
import pandas as pd
from opensoundscape.audio import Audio
from scipy.signal import correlate, correlation_lags
import opensoundscape.signal_processing as sp


class Localizer:
    """
    Localize sound sources from synchronized audio files.

    Algorithm
    ----------
    The user provides a table of class detections from each recorder with timestamps. The user
    also provides a table listing the spatial location of the recorder for each unique audio
    file in the table of detections. The audio recordings must be synchronized
    such that timestamps from each recording correspond to the exact same real-world time.

    Localization of sound events proceeds in three steps:

    1. Grouping of detections into candidate events:

        Simultaneous and spatially clustered detections of a class are selected as targets
        for localization of a single real-world sound event.

        For each detection of a species, the grouping algorithm treats the reciever with the detection
        as a "reference receiver", then selects all detections of the species at the same time and
        within `max_distance_between_receivers` of the reference reciever (the "surrounding detections").
        This selected group of simulatneous, spatially-clustered detections of a class beomes one
        "candidate event" for subsequent localization.

        If the number of recorders in the candidate event is fewer than `min_number_of_receivers`, the
        candidate event is discarded.

        This step creates a highly redundant set of candidate events to localize, because each detection
        is treated separately with its recorder as the 'reference recorder'. Thus, the localized events created by this algorithm may contain multiple instances representing
        the same real-world sound event.


    2. Estimate time delays with cross correlation:

        For each candidate event, the time delay between the reference reciever's detection and the
        surrounding recorders' detections is estimated through generalized cross correlation.

        If the max value of the cross correlation is below `cc_threshold`, the corresponding time delay
        is discarded and not used during localization. This provides a way of filtering out
        undesired time delays that do not correspond to two recordings of the same sound event.

        If the number of time delays in the candidate event is fewer than `min_number_of_receivers`
        after filtering by cross correlation threshold, the candidate event is discarded.

    3. Estiamte positions

        The position of the event is estimated based on the positions and time delays of
        each detection.

        Position estimation from the positions and time delays at a set of receivers is performed
        using one of two algorithms, described in `localization_algorithm` below.

    4. Filter by residual error

        The residual errors represent descrepencies between (a) time of arrival of
        the event at a reciever and (b) distance from reciever to estimated position.

        Estimated positions are discarded if the root mean squared residual error is
        greater than `residual_rmse_threshold` #TODO implement?


    Parameters
    ----------
    files : list
        List of synchronized audio files
    detections : pandas.DataFrame
        DataFrame of detections. The multi-index must be [file, start_time, end_time]
        each column is a class.
    aru_coords : pandas.DataFrame
        DataFrame with index filepath, and columns for x, y, (z) positions of recievers in meters.
        Third coordinate is optional. Localization algorithms are in 2d if columns are (x,y) and
        3d if columns are (x,y,z). Each audio file in `detections` must have a corresponding
        row in `aru_coords` specifiying the position of the reciever.
    sample_rate : int
        Sample rate of the audio files
    min_number_of_receivers : int
        Minimum number of receivers that must detect an event for it to be localized
    max_distance_between_receivers : float (meters)
        Radius around a recorder in which to use other recorders for localizing an event
    localization_algorithm : str, optional
        algorithm to use for estimating the position of a sound event from the positions and
        time delays of a set of detections. [Default: 'gillette']
        Options:
            - 'gillette': linear closed-form algorithm of Gillette and Silverman 2008 [1]
            - 'soundfinder': source? citation? #TODO
    thresholds : dict, optional
        Dictionary of thresholds for each class. Default is None.
    bandpass_ranges : dict, optional
        Dictionary of form {"class name": [low_f, high_f]} for audio bandpass filtering during
        cross correlation. [Default: None] does not bandpass audio. Bandpassing audio to the
        frequency range of the relevant sound is recommended for best cross correlation results.
    max_delay : float, optional
        Maximum absolute value of time delay estimated during cross correlation of two signals
        For instance, 0.2 means that cross correlation will be maximized in the range of
        delays between -0.2 to 0.2 seconds.
        Default: None does not restrict the range, finding delay that maximizes cross correlation
    cc_threshold : float, optional
        Threshold for cross correlation: if the max value of the cross correlation is below
        this value, the corresponding time delay is discarded and not used during localization.
        Default of 0 does not discard any delays.
    cc_filter : str, optional
        Filter to use for generalized cross correlation. See signalprocessing.gcc function for options.
        Default is "phat".

    Methods
    -------
    localize()
        Run the entire localization algorithm on the audio files and detections. This executes the below methods in order.
        group_detections()
            Use a set of score thresholds to filter the detections and ensure that only detections with a minimum number of receivers are returned.
            Saves detections as a pandas.DataFrame to self.grouped_detections.
        cross_correlate()
            Cross correlate the audio files to get time delays of arrival. This is computationally expensive.
            Saves cross correlations as a pandas.DataFrame to self.cross_correlations.
        filter_cross_correlations()
            Filter the cross correlations to remove scores below cc_threshold. This then also ensures at least min_number_of_receivers are present.
            Saves filtered cross correlations as a pandas.DataFrame to self.filtered_cross_correlations.
        localize_events()
            Use the localization algorithm to localize the events from the set of tdoas after filtering.
            Saves locations as a pandas.DataFrame to self.localized_events.


    [1] M. D. Gillette and H. F. Silverman, "A Linear Closed-Form Algorithm for Source Localization From Time-Differences of Arrival," IEEE Signal Processing Letters

    """

    def __init__(
        self,
        files,
        detections,
        aru_coords,
        sample_rate,
        min_number_of_receivers,
        max_distance_between_receivers,
        localization_algorithm="gillette",
        thresholds=None,
        bandpass_ranges=None,
        max_delay=None,
        cc_threshold=0,
        cc_filter="phat",
    ):
        self.files = files
        self.detections = detections
        self.aru_coords = aru_coords
        self.SAMPLE_RATE = sample_rate
        self.min_number_of_receivers = min_number_of_receivers
        self.max_distance_between_receivers = max_distance_between_receivers
        self.localization_algorithm = localization_algorithm
        self.thresholds = thresholds
        self.bandpass_ranges = bandpass_ranges
        self.max_delay = max_delay
        self.cc_threshold = cc_threshold
        self.cc_filter = cc_filter

        # attributes for troubleshooting
        self.files_missing_coordinates = []

        # initialize the outputs of the localizer as None. These will be filled in as the localizer runs.
        self.grouped_detections = None
        self.cross_correlations = None
        self.filtered_cross_correlations = None
        self.localized_events = None

        # check that all files have coordinates in aru_coords
        audio_files_have_coordinates = True
        for file in self.files:
            if str(file) not in self.aru_coords.index:
                audio_files_have_coordinates = False
                self.files_missing_coordinates.append(file)
        if not audio_files_have_coordinates:
            raise UserWarning(
                "WARNING: Not all audio files have corresponding coordinates. Check aru_coords contains a mapping for each file. \n Check the missing files with Localizer.files_missing_coordinates"
            )
        # check that bandpass_ranges have been set for all classes
        if self.bandpass_ranges is not None:
            if set(self.bandpass_ranges.keys()) != set(self.detections.columns):
                warnings.warn(
                    "WARNING: Not all classes have corresponding bandpass ranges. Default behavior will be to not bandpass before cross-correlation for classes that do not have a corresponding bandpass range."
                )  # TODO support one bandpass range for all classes

        # check that thresholds have been set for all classes
        # TODO: remove thresholding. Refactor so that the user passes in a detections df that has already been filtered.
        if self.thresholds is not None:
            if set(self.thresholds.keys()) != set(self.detections.columns):
                warnings.warn(
                    "WARNING: Not all classes have corresponding thresholds. Default behavior will be to drop classes that do not have a corresponding threshold."
                )
        print("Localizer initialized")

    def localize(self):
        """
        Run the entire localization algorithm on the audio files and detections. This executes the below methods in order.
            group_detections()
            cross_correlate()
            filter_cross_correlations()
            localize_events()
        """
        self.group_detections()
        self.cross_correlate()
        self.filter_cross_correlations()
        self.localize_events()
        return self.localized_events

    def group_detections(self):
        """
        Use a set of score thresholds to filter the detections and ensure that only detections with a minimum number of receivers are returned.
        Returns the detections as a pandas.DataFrame and writes it to self.grouped_detections.
        The detections DataFrame has columns:
            time : (start, end) tuple of the detection time in seconds
            reference_file: the reference file for the detection
            other_files: the other files against which cross correlation will be performed
            species: the species of the detection
        """
        if self.detections is None:
            raise UserWarning(
                "No detections exist. Please initialize the Localizer with detections"
            )
        all_sp_detections = []

        # iterate over each species
        for species in self.thresholds.keys():
            df = self.detections.loc[:, [species]]  # must be a dataframe
            detections = Localizer._get_detections(
                df, cnn_score_threshold=self.thresholds[species]
            )
            grouped_detections = Localizer._group_detections(
                detections,
                self.aru_coords,
                self.min_number_of_receivers,
                self.max_distance_between_receivers,
            )
            grouped_detections["species"] = species
            all_sp_detections.append(grouped_detections)
        detections_df = pd.concat(all_sp_detections)
        self.grouped_detections = detections_df
        return detections_df

    def cross_correlate(self):
        """
        Cross correlate the audio files to get time delays of arrival for each time interval where a sound event was detected on at least min_number_of_receivers.
        Returns a pandas.DataFrame and writes it to self.cross_correlations. Warning: this is computationally expensive.
        The DataFrame has columns:
            time : (start, end) tuple of the detection time in seconds
            reference_file: the reference file for the detection
            other_files: list of the other files against which cross correlation will be performed
            species: the species of the detection
            cross_correlations: list of the maximum cross-correlation score for each pair of files
            time_delays: list of the time delays corresponding to the maximal cross-correlation for each pair of files
        """
        if self.bandpass_ranges is None:
            warnings.warn(
                "No bandpass range set. Default behavior will be to not bandpass the audio before cross-correlation."
            )

        if self.max_delay is None:
            warnings.warn(
                "No max delay set. Default behavior will be to allow for any delay between the audio files."
            )
        if self.grouped_detections is None:
            print("No detections exist - running group_detections")
            self.group_detections()()
        # get the cross-correlations
        all_ccs = []
        all_tds = []
        for index, row in self.grouped_detections.iterrows():
            species = row["species"]
            if (
                self.bandpass_ranges is None
            ):  # do not bandpass if no bandpass_ranges set at all
                bandpass_range = None
            else:
                try:
                    bandpass_range = self.bandpass_ranges[species]
                except KeyError:  # do not bandpass if no bandpass range is set for this species
                    bandpass_range = None

            cc, td = Localizer._get_cross_correlations(
                reference_file=row["reference_file"],
                other_files=row["other_files"],
                start_time=row["time"][0],
                end_time=row["time"][1],
                bandpass_range=bandpass_range,
                max_delay=self.max_delay,
                SAMPLE_RATE=self.SAMPLE_RATE,
                cc_filter=self.cc_filter,
            )
            all_ccs.append(cc)
            all_tds.append(td)
        self.cross_correlations = self.grouped_detections.copy()
        self.cross_correlations["cross_correlations"] = all_ccs
        self.cross_correlations["time_delays"] = all_tds
        return self.cross_correlations

    def filter_cross_correlations(self):
        """
        Filter the cross-correlations to only include those that are above a certain threshold. This step also drops any detections where less than min_number_of_receivers are above the threshold.
        Returns a pandas.DataFrame and writes it to self.filtered_cross_correlations.
        The DataFrame has columns:
            time : (start, end) tuple of the detection time in seconds
            reference_file: the reference file for the detection
            other_files: list of the other files against which cross correlation will be performed
            species: the species of the detection
            cross_correlations: list of the maximum cross-correlation score for each pair of files
            time_delays: list of the time delays corresponding to the maximal cross-correlation for each pair of files
        """
        if self.cross_correlations is None:
            print("No cross correlations exist - running cross_correlate")
            self.cross_correlate()
        # filter the cross-correlations
        above_threshold = [
            cc > self.cc_threshold
            for cc in self.cross_correlations["cross_correlations"]
        ]

        n_before = len(self.cross_correlations)  # number of rows before filtering

        filtered_ccs = []
        filtered_files = []
        filtered_tdoas = []
        for i in range(len(self.cross_correlations)):
            mask = above_threshold[i]
            cc = self.cross_correlations["cross_correlations"].iloc[i]
            other_files = np.array(self.cross_correlations["other_files"].iloc[i])
            tdoa = np.array(self.cross_correlations["time_delays"].iloc[i])

            filtered_ccs.append(cc[mask])
            filtered_files.append(other_files[mask])
            filtered_tdoas.append(tdoa[mask])

        filtered_cross_correlations = self.cross_correlations.copy()

        filtered_cross_correlations["cross_correlations"] = filtered_ccs
        filtered_cross_correlations["other_files"] = filtered_files
        filtered_cross_correlations["time_delays"] = filtered_tdoas

        # Filter by the cc scores. If less than min_number_of_receivers have cc_score above threshold, drop them.
        ccs = [
            np.array(scores)
            for scores in filtered_cross_correlations["cross_correlations"]
        ]
        num_ccs_above_threshold = [sum(a > self.cc_threshold) for a in ccs]
        mask = np.array(num_ccs_above_threshold) >= self.min_number_of_receivers - 1
        filtered_cross_correlations = filtered_cross_correlations[mask]

        n_after = len(filtered_cross_correlations)  # number of rows after filtering
        print(f"{n_before - n_after} rows deleted")
        self.filtered_cross_correlations = filtered_cross_correlations
        return filtered_cross_correlations

    def localize_events(self):
        """
        Localize the events using the localization algorithm specified in self.localization_algorithm. Returns a pandas.DataFrame with the results and writes it to self.localizations
        The columns of the DataFrame are:
            time : (start, end) tuple of the detection time in seconds
            reference_file: the reference file for the detection
            other_files: list of the other files against which cross correlation will be performed
            species: the species of the detection
            cross_correlations: list of the maximum cross-correlation score for each pair of files
            time_delays: list of the time delays corresponding to the maximal cross-correlation for each pair of files
            predicted_x: the predicted x coordinate of the event
            predicted_y: the predicted y coordinate of the event
            predicted_z: the predicted z coordinate of the event
            tdoa_error: the residuals in the tdoas against what would be expected from the predicted location.
        """
        if self.filtered_cross_correlations is None:
            print(
                "No filtered cross_correlations exist - running filter_cross_correlations"
            )
            self.filter_cross_correlations()
        localized = self.filtered_cross_correlations.copy()
        locations = []

        for index, row in self.filtered_cross_correlations.iterrows():
            reference = row["reference_file"]
            others = row["other_files"]
            reference_coords = self.aru_coords.loc[reference]
            others_coords = [self.aru_coords.loc[i] for i in others]
            all_coords = [reference_coords] + others_coords
            # add 0 tdoa for reference receiver
            delays = np.insert(row["time_delays"], 0, 0)

            location = localize(
                all_coords, delays, algorithm=self.localization_algorithm
            )
            locations.append(location)
        localized["predicted_location"] = locations
        self.localized_events = localized
        return localized

    def _get_cross_correlations(
        reference_file,
        other_files,
        start_time,
        end_time,
        bandpass_range,
        max_delay,
        SAMPLE_RATE,
        cc_filter,
    ):
        """
        Gets the maximal cross correlations and the time-delay (in s) corresponding to that cross correlation between
        the reference_file and other_files. Setting max_delay ensures that only cross-correlations
        +/- a certain time-delay are returned. i.e if a sound can be a maximum of +/-
        ----
        args:
            reference_file: Path to reference file.
            other_files: List of paths to the other files which will be cross-correlated against reference_file
            start_time: start of time segment (in seconds) to be cross-correlated
            end_time: end of time segment (in seconds) to be cross-correlated.
            bandpass_range: [lower, higher] of bandpass range. If None, no bandpass filter is applied.
            max_delay: the maximum time (in seconds) to return cross_correlations for. i.e. if the best cross correlation
                        occurs for a time-delay greater than max_delay, the function will not return it, instead it will return
                        the maximal cross correlation within +/- max_delay
            SAMPLE_RATE: the sampling rate of the audio.
            cc_filter: the filter to use for cross-correlation. see signalprocessing.gcc for options. Options currently are "phat" or "cc"
        returns:
            ccs: list of maximal cross-correlations for each pair of files.
            time_differences: list of time differences (in seconds) that yield the maximal cross-correlation.
        """
        if bandpass_range is None:
            # no bandpass filter
            reference_audio = Audio.from_file(
                reference_file, offset=start_time, duration=end_time - start_time
            )
            other_audio = [
                Audio.from_file(i, offset=start_time, duration=end_time - start_time)
                for i in other_files
            ]
        else:
            lower = min(bandpass_range)
            higher = max(bandpass_range)

            reference_audio = Audio.from_file(
                reference_file, offset=start_time, duration=end_time - start_time
            ).bandpass(lower, higher, order=9)
            other_audio = [
                Audio.from_file(
                    i, offset=start_time, duration=end_time - start_time
                ).bandpass(lower, higher, order=9)
                for i in other_files
            ]
        ccs = np.zeros(len(other_audio))
        time_difference = np.zeros(len(other_audio))
        for index, audio_object in enumerate(other_audio):
            delay, cc = sp.tdoa(
                audio_object.samples,
                reference_audio.samples,
                cc_filter=cc_filter,
                sample_rate=SAMPLE_RATE,
                return_max=True,
                max_delay=max_delay,
            )

            time_difference[index] = delay
            ccs[index] = cc

        return ccs, time_difference

    def _get_detections(predictions_df, cnn_score_threshold):
        """
        Takes the predictions_df of CNN scores *FOR A SINGLE SPECIES*, chooses only detections > cnn_score_threshold
        and outputs a dictionary of times at which events were detected, and the ARU files they were detected in.
        args:
            predictions_array: a dataframe with multi-index of (file, start_time, end_time) with a column that is values for model predictions
            *FOR A SINGLE SPECIES*
            cnn_score_threshold: the minimum CNN score needed for a time-window to be considered a detection.
        returns:
            A dictionary of predictions, with key (start_time, end_time), and value list of files with detection triggered
            e.g. {(0.0,2.0): [ARU_0.mp3. ARU_1.mp3]}
        """
        # get the detections from the predictions
        # Threshold the scores to above cnn_score_threshold
        booleans = (
            predictions_df.loc[:, :, :] > cnn_score_threshold
        )  # find rows above threshold
        indices = (
            booleans[booleans].dropna().index
        )  # choose just those rows. dropna required to drop the others
        recorders = indices.get_level_values(
            0
        )  # get the list of recorders out of the multi-index
        indices = indices.droplevel(level=0)  # drop the recorders

        dataframe = pd.DataFrame(
            data=recorders, index=indices
        )  # df with index (start_time, end_time)
        dataframe = (
            dataframe.sort_index()
        )  # done to ensure speed-up and not get performancewarning
        recorders_list = []
        for idx in dataframe.index.unique():
            recorders_in_time = dataframe.loc[idx].values
            recorders_in_time = [
                i[0] for i in recorders_in_time
            ]  # to get recorder path string out of numpy array
            recorders_list.append(recorders_in_time)
        return dict(zip(dataframe.index.unique(), recorders_list))

    def _group_detections(
        detections, aru_coords, min_number_of_receivers, max_distance_between_receivers
    ):
        """
        Takes the detections dictionary and groups detections that are within max_distance_between_receivers of each other.
        args:
            detections: a dictionary of detections, with key (start_time, end_time), and value list of files with detection triggered
            aru_coords: a dictionary of aru coordinates, with key aru file path, and value (x,y) coordinates
            max_distance_between_receivers: the maximum distance between recorders to consider a detection as a single event
        returns:
            A dictionary of grouped detections, with key (start_time, end_time), and value list of files with detection triggered
            e.g. {(0.0,2.0): [ARU_0.mp3. ARU_1.mp3]}
        """
        from itertools import product

        # Group recorders based on being within < max_distance_between_receivers.
        # recorders_in_distance is dictionary in
        # form {ARU_0.mp3: [ARU_1.mp3, ARU_2.mp3...] for all recorders within max_distance_between_receivers }
        recorders_in_distance = dict()

        aru_files = aru_coords.index
        for aru in aru_files:  # loop over the aru files
            pos_aru = np.array(aru_coords.loc[aru])  # position of receiver
            other_arus = np.array(aru_coords)
            distances = other_arus - pos_aru
            euclid_distances = [np.linalg.norm(d) for d in distances]

            mask = [
                0 <= i <= max_distance_between_receivers for i in euclid_distances
            ]  # boolean mask
            recorders_in_distance[aru] = list(aru_files[mask])

        times = []
        reference_files = []
        other_files = []

        for time_segment in detections.keys():  # iterate through all the time-segments
            for file in detections[
                time_segment
            ]:  # iterate through each file with a call detected in this time-segment
                reference = file  # set this file to be reference
                others = [
                    f for f in detections[time_segment] if f != reference
                ]  # All the other receivers
                others_in_distance = [
                    aru for aru in others if aru in recorders_in_distance[reference]
                ]  # only the receivers that are close enough

                if (
                    len(others_in_distance) + 1 >= min_number_of_receivers
                ):  # minimum number of receivers needed to localize.
                    times.append(time_segment)
                    reference_files.append(reference)
                    other_files.append(others_in_distance)

        grouped_detections = pd.DataFrame(
            data=zip(times, reference_files, other_files),
            columns=["time", "reference_file", "other_files"],
        )
        return grouped_detections


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


def localize(receiver_positions, tdoas, algorithm, speed_of_sound=343):
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
    speed_of_sound=343,
    invert_alg="gps",  # options: 'gps'
    center=True,  # True for original Sound Finder behavior
    pseudo=True,  # False for original Sound Finder
):

    """
    Use the soundfinder algorithm to perform TDOA localization on a sound event
    Localize a sound event given relative arrival times at multiple receivers.
    This function implements a localization algorithm from the
    equations described in the class handout ("Global Positioning
    Systems"). Localization can be performed in a global coordinate
    system in meters (i.e., UTM), or relative to recorder positions
    in meters.
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
    """
    # make sure our inputs follow consistent format
    receiver_positions = np.array(receiver_positions).astype("float64")
    arrival_times = np.array(arrival_times).astype("float64")

    # The number of dimensions in which to perform localization
    dim = receiver_positions.shape[1]

    ##### Shift coordinate system to center receivers around origin #####
    if center:
        warnings.warn("centering")
        p_mean = np.mean(receiver_positions, 0)
        receiver_positions = np.array([p - p_mean for p in receiver_positions])
    else:
        warnings.warn("not centering")

    ##### Compute B, a, and e #####
    # Find the pseudorange, rho, for each recorder
    # pseudorange (minus a constant) ~= distances from source to each receiver
    rho = np.array([arrival_times * (-1 * speed_of_sound)]).T

    # Concatenate the pseudorange column to form matrix B
    B = np.concatenate((receiver_positions, rho), axis=1)

    # Vector of ones
    e = np.ones(receiver_positions.shape[0])

    # The vector of squared Lorentz norms
    a = 0.5 * np.apply_along_axis(lorentz_ip, axis=1, arr=B)

    # choose between two algorithms to invert the matrix
    if invert_alg == "lstsq":
        raise NotImplementedError
        # Compute B+ * a and B+ * e
        # using closest equivalent to R's solve(qr(B), e)
        # Bplus_e = np.linalg.lstsq(B, e, rcond=None)[0]
        # Bplus_a = np.linalg.lstsq(B, a, rcond=None)[0]

    else:  # invert_alg == 'gps' or 'special'
        ## Compute B+ = (B^T \* B)^(-1) \* B^T
        # B^T * B

        to_invert = np.matmul(B.T, B)

        try:
            inverted = np.linalg.inv(to_invert)

        except np.linalg.LinAlgError as err:
            # for 'gps' algorithm, simply fail
            if invert_alg == "gps":
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
            else:  # invert_alg == 'special'
                warnings.warn("7")
                Bplus_e = np.linalg.lstsq(B, e, rcond=None)[0]
                Bplus_a = np.linalg.lstsq(B, a, rcond=None)[0]

        else:  # inversion of the matrix succeeded
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
        # Return the solution with the lower error in pseudorange
        # (Error in pseudorange is the final value of the position/solution vector)
        if abs(u0[-1]) <= abs(u1[-1]):
            return u0[0:-1]  # drop the final value, which is the error
        else:
            return u1[0:-1]  # drop the final value, which is the error

    else:
        # This was the return method used in the original Sound Finder,
        # but it gives worse performance

        # Compute sum of squares discrepancies for each solution
        s0 = float(np.sum((np.matmul(B, u0) - np.add(a, lamb[0] * e)) ** 2))
        s1 = float(np.sum((np.matmul(B, u1) - np.add(a, lamb[1] * e)) ** 2))

        # Return the solution with lower sum of squares discrepancy
        if s0 < s1:
            return u0[0:-1]  # drop the final value, which is the error
        else:
            return u1[0:-1]  # drop the final value, which is the error


def gillette_localize(
    receiver_positions, arrival_times, reference_receiver=0, speed_of_sound=343
):
    """
    Uses the Gillette and Silverman (2008) localization algorithm to localize a sound event from a set of TDOAs.
    Args:
        receiver_positions: a list of [x,y] or [x,y,z] positions for each receiver
            Positions should be in meters, e.g., the UTM coordinate system.
        arrival_times: a list of TDOA times (arrival times) for each receiver
            The times should be in seconds.
        reference_receiver: the index of the reference receiver (the receiver against which all other arrival times are measured)
            default is 0 (the first receiver)
        speed_of_sound: speed of sound in m/s
    Returns:
        coords: a tuple of (x,y,z) coordinates of the sound source
    """

    # check that these delays are with reference to one receiver (the reference receiver). If not, raise an error
    if np.min(arrival_times) != 0:
        raise ValueError(
            "Arrival times must be relative to a reference receiver. Therefore the minimum arrival time must be 0 (corresponding to arrival at the reference receiver) None of your TDOAs are zero. Please check your arrival_times."
        )

    # make sure our inputs follow consistent format
    receiver_positions = np.array(receiver_positions).astype("float64")
    arrival_times = np.array(arrival_times).astype("float64")

    # The number of dimensions in which to perform localization
    dim = receiver_positions.shape[1]

    # find which is the reference receiver and reorder, so reference receiver is first
    ref_receiver = np.argmin(arrival_times)
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


def calc_tdoa_residuals(
    reference_receiver, other_receivers, tdoas, position_estimate, speed_of_sound
):
    """
    Calculate the residuals of the TDOA localization algorithm
    Args:
        reference_receiver: The coordinates (in m) of the reference receiver
        other_receivers: The coordinates (in m) of the other receivers
        tdoas: The time delays of arrival for the sound. each tdoa should correspond to the other receiver in the same index
        position_estimate: The estimated position of the sound
        speed_of_sound: The speed of sound in m/s
    Returns:
        an array of length len(other_receivers) containing the residuals of the TDOs.
    """
    # ensure all are numpy arrays
    reference_receiver = np.array(reference_receiver)
    other_receivers = np.array(other_receivers)
    tdoas = np.array(tdoas)
    position_estimate = np.array(position_estimate)

    # Calculate the TDOA residuals
    tdoa_residuals = []
    arrival_at_reference = (
        np.linalg.norm(reference_receiver - position_estimate) / speed_of_sound
    )
    arrival_at_others = np.array(
        [
            np.linalg.norm(i - position_estimate) / speed_of_sound
            for i in other_receivers
        ]
    )

    expected_tdoas = arrival_at_others - arrival_at_reference

    residuals = expected_tdoas - tdoas

    return residuals
