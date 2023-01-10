"""classes for pytorch machine learning models in opensoundscape

For tutorials, see notebooks on opensoundscape.org
"""
from pathlib import Path
import warnings
import copy
import os
import types
import yaml

import numpy as np
import pandas as pd
from pandas.core.indexes.multi import MultiIndex

import torch
import torch.optim as optim
import torch.nn.functional as F
from torch.utils.data import DataLoader

import opensoundscape
from opensoundscape.torch.architectures import cnn_architectures
from opensoundscape.torch.models.utils import (
    BaseModule,
    apply_activation_layer,
)
from opensoundscape.preprocess.preprocessors import SpectrogramPreprocessor
from opensoundscape.torch.loss import (
    BCEWithLogitsLoss_hot,
    CrossEntropyLoss_hot,
    ResampleLoss,
)
from opensoundscape.torch.safe_dataset import SafeDataset
from opensoundscape.torch.datasets import AudioFileDataset, AudioSplittingDataset
from opensoundscape.torch.architectures.cnn_architectures import inception_v3
from opensoundscape.metrics import (
    predict_multi_target_labels,
    predict_single_target_labels,
    single_target_metrics,
    multi_target_metrics,
)


class CNN(BaseModule):
    """
    Generic CNN Model with .train(), .predict(), and .save()

    flexible architecture, optimizer, loss function, parameters

    for tutorials and examples see opensoundscape.org

    Args:
        architecture:
            *EITHER* a pytorch model object (subclass of torch.nn.Module),
            for example one generated with the `cnn_architectures` module
            *OR* a string matching one of the architectures listed by
            cnn_architectures.list_architectures(), eg 'resnet18'.
            - If a string is provided, uses default parameters
                (including use_pretrained=True)
                Note: For resnet architectures, if num_channels != 3,
                averages the conv1 weights across all channels.
        classes:
            list of class names. Must match with training dataset classes if training.
        single_target:
            - True: model expects exactly one positive class per sample
            - False: samples can have any number of positive classes
            [default: False]

    """

    def __init__(
        self,
        architecture,
        classes,
        sample_duration,
        single_target=False,
        preprocessor_class=SpectrogramPreprocessor,
        sample_shape=[224, 224, 3],
    ):

        super(CNN, self).__init__()

        self.name = "CNN"

        # model characteristics
        self.current_epoch = 0
        self.classes = classes
        self.single_target = single_target  # if True: predict only class w max score
        self.opensoundscape_version = opensoundscape.__version__
        # number of samples to preprocess and log to wandb during train/predict
        self.wandb_logging = dict(
            n_preview_samples=8,  # before train/predict, log n random samples
            top_samples_classes=None,  # specify list of classes to see top samples from
            n_top_samples=3,  # after prediction, log n top scoring samples per class
        )
        self.loss_fn = None
        self.train_loader = None
        self.scheduler = None

        ### architecture ###
        # can be a pytorch CNN such as Resnet18 or a custom object
        # must have .forward(), .train(), .eval(), .to(), .state_dict()
        # for convenience, also allows user to provide string matching
        # a key from cnn_architectures.ARCH_DICT
        num_channels = sample_shape[2]
        if type(architecture) == str:
            assert architecture in cnn_architectures.list_architectures(), (
                f"architecture must be a pytorch model object or string matching "
                f"one of cnn_architectures.list_architectures() options. Got {architecture}"
            )
            self.architecture_name = architecture
            architecture = cnn_architectures.ARCH_DICT[architecture](
                len(classes), num_channels=num_channels
            )
        else:
            assert issubclass(
                type(architecture), torch.nn.Module
            ), "architecture must be a string or an instance of a subclass of torch.nn.Module"
            if num_channels != 3:
                warnings.warn(
                    f"Make sure your architecture expects the number of channels in "
                    f"your input samples ({num_channels}). "
                    f"Pytorch architectures expect 3 channels by default."
                )
            self.architecture_name = str(type(architecture))
        self.network = architecture

        ### network device ###
        # automatically gpu (default is 'cuda:0') if available
        # can override after init, eg model.device='cuda:1'
        # network and samples are moved to gpu during training/inference
        # devices could be 'cuda:0', torch.device('cuda'), torch.device('cpu')
        if torch.cuda.is_available():
            self.device = torch.device("cuda")
        else:
            self.device = torch.device("cpu")

        ### sample loading/preprocessing ###
        self.preprocessor = preprocessor_class(
            sample_duration=sample_duration, out_shape=sample_shape
        )

        ### loss function ###
        if self.single_target:  # use cross entropy loss by default
            self.loss_cls = CrossEntropyLoss_hot
        else:  # for multi-target, use binary cross entropy
            self.loss_cls = BCEWithLogitsLoss_hot

        ### training parameters ###
        # optimizer
        self.opt_net = None  # don't set directly. initialized during training
        self.optimizer_cls = optim.SGD  # or torch.optim.Adam, etc

        # instead of putting "params" key here, we only add it during
        # _init_optimizer, just before initializing the optimizers
        # this avoids an issue when re-loading a model of
        # having the wrong .parameters() list
        self.optimizer_params = {
            # "params": self.network.parameters(),
            "lr": 0.01,
            "momentum": 0.9,
            "weight_decay": 0.0005,
        }

        # lr_scheduler
        self.lr_update_interval = 10  # update learning rates every # epochs
        self.lr_cooling_factor = 0.7  # multiply learning rates by # on each update

        ### metrics ###
        self.prediction_threshold = 0.5
        # override self.eval() to change what metrics are
        # computed and displayed during training/validation

        ### Logging ###
        self.log_file = None  # specify a path to save output to a text file
        self.logging_level = 1  # 0 for nothing, 1,2,3 for increasing logged info
        self.verbose = 1  # 0 for nothing, 1,2,3 for increasing printed output

        # dictionaries to store accuracy metrics & loss for each epoch
        self.train_metrics = {}
        self.valid_metrics = {}
        self.loss_hist = {}  # could add TensorBoard tracking

    def _log(self, message, level=1):
        txt = str(message)
        if self.logging_level >= level and self.log_file is not None:
            with open(self.log_file, "a") as logfile:
                logfile.write(txt + "\n")
        if self.verbose >= level:
            print(txt)

    def _init_optimizer(self):
        """initialize an instance of self.optimizer

        This function is called during .train() so that the user
        has a chance to swap/modify the optimizer before training.

        To modify the optimizer, change the value of
        self.optimizer_cls and/or self.optimizer_params
        prior to calling .train().
        """
        param_dict = self.optimizer_params
        param_dict["params"] = self.network.parameters()
        return self.optimizer_cls([param_dict])

    def _init_loss_fn(self):
        """initialize an instance of self.loss_cls

        This function is called during .train() so that the user
        has a chance to change the loss function before training.
        """
        self.loss_fn = self.loss_cls()

    def _init_dataloader(
        self, safe_dataset, batch_size=64, num_workers=1, shuffle=False
    ):
        """initialize dataloader for training

        Override this function to use a different DataLoader or sampler
        """
        return DataLoader(
            safe_dataset,
            batch_size=batch_size,
            shuffle=shuffle,
            num_workers=num_workers,
            pin_memory=True,
        )

    def _set_train(self, train_df, batch_size, num_workers):
        """Prepare network for training on train_df

        Args:
            batch_size: number of training files to load/process before
                        re-calculating the loss function and backpropagation
            num_workers: parallelization (number of cores or cpus)

        Effects:
            Sets up the optimization, loss function, and network
        """

        ###########################
        # Move network to device  #
        ###########################
        self.network.to(self.device)

        ######################
        # Dataloader setup #
        ######################
        train_dataset = AudioFileDataset(train_df, self.preprocessor)
        train_dataset.bypass_augmentations = False

        # SafeDataset loads a new sample if loading a sample throws an error
        # indices of bad samples are appended to ._invalid_indices
        train_safe_dataset = SafeDataset(
            train_dataset, invalid_sample_behavior="substitute"
        )

        # train_loader samples batches of images + labels from training set
        self.train_loader = self._init_dataloader(
            train_safe_dataset,
            batch_size=batch_size,
            num_workers=num_workers,
            shuffle=True,
        )

        ###########################
        # Setup loss function     #
        ###########################
        self._init_loss_fn()

        ######################
        # Optimization setup #
        ######################

        # Setup optimizer parameters for each network component
        # Note: we re-create bc the user may have changed self.optimizer_cls
        # If optimizer already exists, keep the same state dict
        # (for instance, user may be resuming training w/saved state dict)
        if self.opt_net is not None:
            optim_state_dict = self.opt_net.state_dict()
            self.opt_net = self._init_optimizer()
            self.opt_net.load_state_dict(optim_state_dict)
        else:
            self.opt_net = self._init_optimizer()

        # Set up learning rate cooling schedule
        self.scheduler = optim.lr_scheduler.StepLR(
            self.opt_net,
            step_size=self.lr_update_interval,
            gamma=self.lr_cooling_factor,
            last_epoch=self.current_epoch - 1,
        )

    def _train_epoch(self, train_loader, wandb_session=None):
        """perform forward pass, loss, and backpropagation for one epoch

        If wandb_session is passed, logs progress to wandb run

        Args:
            train_loader: DataLoader object to create samples
            wandb_session: a wandb session to log to
                - pass the value returned by wandb.init() to progress log to a
                Weights and Biases run
                - if None, does not log to wandb

        Returns: (targets, predictions, scores) on training files
        """
        self.network.train()

        total_tgts = []
        total_preds = []
        total_scores = []
        batch_loss = []

        for batch_idx, batch_data in enumerate(train_loader):
            # load a batch of images and labels from the train loader
            # all augmentation occurs in the Preprocessor (train_loader)
            batch_tensors = batch_data["X"].to(self.device)
            batch_labels = batch_data["y"].to(self.device)
            if len(self.classes) > 1:  # squeeze one dimension [1,2] -> [1,1]
                batch_labels = batch_labels.squeeze(1)

            ####################
            # Forward and loss #
            ####################

            # forward pass: feature extractor and classifier
            logits = self.network.forward(batch_tensors)

            # save targets and predictions
            total_scores.append(logits.detach().cpu().numpy())
            total_tgts.append(batch_labels.detach().cpu().numpy())

            # generate boolean predictions
            if self.single_target:  # predict highest scoring class only
                batch_preds = predict_single_target_labels(scores=logits)
            else:  # multi-target: predict 0 or 1 based on a fixed threshold
                batch_preds = predict_multi_target_labels(
                    scores=torch.sigmoid(logits), threshold=self.prediction_threshold
                )
            total_preds.append(batch_preds.detach().int().cpu().numpy())

            # calculate loss
            loss = self.loss_fn(logits, batch_labels)

            # save loss for each batch; later take average for epoch

            batch_loss.append(loss.detach().cpu().numpy())

            #############################
            # Backward and optimization #
            #############################
            # zero gradients for optimizer
            self.opt_net.zero_grad()
            # backward pass: calculate the gradients
            loss.backward()
            # update the network using the gradients*lr
            self.opt_net.step()

            ###########
            # Logging #
            ###########
            # log basic train info (used to print every batch)
            if batch_idx % self.log_interval == 0:
                # show some basic progress metrics during the epoch
                N = len(train_loader)
                self._log(
                    f"Epoch: {self.current_epoch} "
                    f"[batch {batch_idx}/{N}, {100 * batch_idx / N :.2f}%] "
                )

                # Log the Jaccard score and Hamming loss, and Loss function
                epoch_loss_avg = np.mean(batch_loss)
                self._log(f"\tDistLoss: {epoch_loss_avg:.3f}")

                # Evaluate with model's eval function
                tgts = batch_labels.int().detach().cpu().numpy()
                # preds = batch_preds.int().detach().cpu().numpy()
                scores = logits.int().detach().cpu().numpy()
                self.eval(tgts, scores, logging_offset=-1)

        # update learning parameters each epoch
        self.scheduler.step()

        # save the loss averaged over all batches
        self.loss_hist[self.current_epoch] = np.mean(batch_loss)

        if wandb_session is not None:
            wandb_session.log({"loss": np.mean(batch_loss)})

        # return targets, preds, scores
        total_tgts = np.concatenate(total_tgts, axis=0)
        total_preds = np.concatenate(total_preds, axis=0)
        total_scores = np.concatenate(total_scores, axis=0)

        return total_tgts, total_preds, total_scores

    def _generate_wandb_config(self):
        # create a dictinoary of parameters to save for this run
        wandb_config = dict(
            architecture=self.architecture_name,
            sample_duration=self.preprocessor.sample_duration,
            cuda_device_count=torch.cuda.device_count(),
            mps_available=torch.backends.mps.is_available(),
            classes=self.classes,
            single_target=self.single_target,
            opensoundscape_version=self.opensoundscape_version,
        )
        if "weight_decay" in self.optimizer_params:
            wandb_config["l2_regularization"] = self.optimizer_params["weight_decay"]
        else:
            wandb_config["l2_regularization"] = "n/a"

        if "lr" in self.optimizer_params:
            wandb_config["learning_rate"] = self.optimizer_params["lr"]
        else:
            wandb_config["learning_rate"] = "n/a"

        try:
            wandb_config["sample_shape"] = self.preprocessor.pipeline.to_img.params[
                "shape"
            ] + [self.preprocessor.pipeline.to_img.params["channels"]]
        except:
            wandb_config["sample_shape"] = "n/a"

        return wandb_config

    def train(
        self,
        train_df,
        validation_df=None,
        epochs=1,
        batch_size=1,
        num_workers=0,
        save_path=".",
        save_interval=1,  # save weights every n epochs
        log_interval=10,  # print metrics every n batches
        validation_interval=1,  # compute validation metrics every n epochs
        invalid_samples_log="./invalid_training_samples.log",
        wandb_session=None,
    ):
        """train the model on samples from train_dataset

        If customized loss functions, networks, optimizers, or schedulers
        are desired, modify the respective attributes before calling .train().

        Args:
            train_df:
                a dataframe of files and labels for training the model
                - either has index `file` or multi-index (file,start_time,end_time)
            validation_df:
                a dataframe of files and labels for evaluating the model
                [default: None means no validation is performed]
            epochs:
                number of epochs to train for
                (1 epoch constitutes 1 view of each training sample)
            batch_size:
                number of training files simultaneously passed through
                forward pass, loss function, and backpropagation
            num_workers:
                number of parallel CPU tasks for preprocessing
                Note: use 0 for single (root) process (not 1)
            save_path:
                location to save intermediate and best model objects
                [default=".", ie current location of script]
            save_interval:
                interval in epochs to save model object with weights [default:1]
                Note: the best model is always saved to best.model
                in addition to other saved epochs.
            log_interval:
                interval in batches to print training loss/metrics
            validation_interval:
                interval in epochs to test the model on the validation set
                Note that model will only update it's best score and save best.model
                file on epochs that it performs validation.
            invalid_samples_log:
                file path: log all samples that failed in preprocessing
                (file written when training completes)
                - if None,  does not write a file
            wandb_session: a wandb session to log to
                - pass the value returned by wandb.init() to progress log to a
                Weights and Biases run
                - if None, does not log to wandb
                For example:
                ```
                import wandb
                wandb.login(key=api_key) #find your api_key at https://wandb.ai/settings
                session = wandb.init(enitity='mygroup',project='project1',name='first_run')
                ...
                model.train(...,wandb_session=session)
                session.finish()
                ```

        Effects:
            If wandb_session is provided, logs progress and samples to Weights
            and Biases. A random set of training and validation samples
            are preprocessed and logged to a table. Training progress, loss,
            and metrics are also logged.
            Use self.wandb_logging dictionary to change the number of samples
            logged.
        """

        ### Input Validation ###
        class_err = (
            "Train and validation datasets must have same classes "
            "and class order as model object."
        )
        assert list(self.classes) == list(train_df.columns), class_err
        if validation_df is not None:
            assert list(self.classes) == list(validation_df.columns), class_err

        # Validation: warn user if no validation set
        if validation_df is None:
            warnings.warn(
                "No validation set was provided. Model will be "
                "evaluated using the performance on the training set."
            )

        # Initialize attributes
        self.log_interval = log_interval
        self.save_interval = save_interval
        self.save_path = save_path

        # Initialize Weights and Biases (wandb) logging ###
        if wandb_session is not None:

            # update the run config with information about the model
            wandb_session.config.update(self._generate_wandb_config())

            # update the run config with training parameters
            wandb_session.config.update(
                dict(
                    epochs=epochs,
                    batch_size=batch_size,
                    num_workers=num_workers,
                    lr_update_interval=self.lr_update_interval,
                    lr_cooling_factor=self.lr_cooling_factor,
                    optimizer_cls=self.optimizer_cls,
                    model_save_path=Path(save_path).resolve(),
                )
            )

            # log tables of preprocessed samples
            wandb_session.log(
                {
                    "Samples / training samples w/augmentation": opensoundscape.wandb.wandb_table(
                        AudioFileDataset(
                            train_df, self.preprocessor, bypass_augmentations=False
                        ),
                        self.wandb_logging["n_preview_samples"],
                    ),
                    "Samples / training samples w/o augmentation": opensoundscape.wandb.wandb_table(
                        AudioFileDataset(
                            train_df, self.preprocessor, bypass_augmentations=True
                        ),
                        self.wandb_logging["n_preview_samples"],
                    ),
                    "Samples / validation samples": opensoundscape.wandb.wandb_table(
                        AudioFileDataset(
                            validation_df, self.preprocessor, bypass_augmentations=True
                        ),
                        self.wandb_logging["n_preview_samples"],
                    ),
                }
            )

        ### Set Up Loss and Optimization ###
        self._set_train(train_df, batch_size, num_workers)
        self.best_score = 0.0
        self.best_epoch = 0

        ### Train ###

        for epoch in range(epochs):
            # 1 epoch = 1 view of each training file
            # loss fn & backpropogation occurs after each batch

            ### Training ###
            self._log(f"\nTraining Epoch {self.current_epoch}")
            train_targets, _, train_scores = self._train_epoch(
                self.train_loader,
                wandb_session,
            )

            ### Evaluate ###
            train_score, self.train_metrics[self.current_epoch] = self.eval(
                train_targets, train_scores
            )
            if wandb_session is not None:
                wandb_session.log({"training": self.train_metrics[self.current_epoch]})

            #### Validation ###
            if validation_df is not None and epoch % validation_interval == 0:
                self._log("\nValidation.")
                validation_scores = self.predict(
                    validation_df,
                    batch_size=batch_size,
                    num_workers=num_workers,
                    activation_layer="softmax_and_logit"
                    if self.single_target
                    else None,
                    split_files_into_clips=False,
                )
                validation_targets = validation_df.values
                validation_scores = validation_scores.values

                validation_score, self.valid_metrics[self.current_epoch] = self.eval(
                    validation_targets, validation_scores
                )
                score = validation_score
            else:  # Evaluate model w/train_score if no validation_df given
                score = train_score

            if wandb_session is not None:
                wandb_session.log(
                    {"validation": self.valid_metrics[self.current_epoch]}
                )

            ### Save ###
            if (
                self.current_epoch + 1
            ) % self.save_interval == 0 or epoch == epochs - 1:
                self._log("Saving weights, metrics, and train/valid scores.", level=2)

                self.save(f"{self.save_path}/epoch-{self.current_epoch}.model")

            # if this is the best score, update & save weights to best.model
            if score > self.best_score:
                self.best_score = score
                self.best_epoch = self.current_epoch
                self._log("Updating best model", level=2)
                self.save(f"{self.save_path}/best.model")

            if wandb_session is not None:
                wandb_session.log({"epoch": epoch})
            self.current_epoch += 1

        ### Logging ###
        self._log("Training complete", level=2)
        self._log(
            f"\nBest Model Appears at Epoch {self.best_epoch} "
            f"with Validation score {self.best_score:.3f}."
        )

        # warn the user if there were invalid samples (samples that failed to preprocess)
        invalid_samples = self.train_loader.dataset.report(log=invalid_samples_log)
        self._log(
            f"{len(invalid_samples)} of {len(train_df)} total training "
            f"samples failed to preprocess",
            level=2,
        )
        self._log(f"List of invalid samples: {invalid_samples}", level=3)

    def eval(self, targets, scores, logging_offset=0):
        """compute single-target or multi-target metrics from targets and scores

        By default, the overall model score is "map" (mean average precision)
        for multi-target models (self.single_target=False) and "f1" (average
        of f1 score across classes) for single-target models).

        Override this function to use a different set of metrics.
        It should always return (1) a single score (float) used as an overall
        metric of model quality and (2) a dictionary of computed metrics

        Args:
            targets: 0/1 for each sample and each class
            scores: continuous values in 0/1 for each sample and class
            logging_offset: modify verbosity - for example, -1 will reduce
                the amount of printing/logging by 1 level
        """

        # remove all samples with NaN for a prediction
        targets = targets[~np.isnan(scores).any(axis=1), :]
        scores = scores[~np.isnan(scores).any(axis=1), :]

        if len(scores) < 1:
            warnings.warn("Recieved empty list of predictions (or all nan)")
            return np.nan, np.nan

        if self.single_target:
            metrics_dict = single_target_metrics(targets, scores)
        else:
            metrics_dict = multi_target_metrics(
                targets, scores, self.classes, self.prediction_threshold
            )

        # decide what to print/log:
        self._log("Metrics:")
        if not self.single_target:
            self._log(f"\tMAP: {metrics_dict['map']:0.3f}", level=1 - logging_offset)
            self._log(
                f"\tAU_ROC: {metrics_dict['au_roc']:0.3f} ", level=2 - logging_offset
            )
        self._log(
            f"\tJacc: {metrics_dict['jaccard']:0.3f} "
            f"Hamm: {metrics_dict['hamming_loss']:0.3f} ",
            level=2 - logging_offset,
        )
        self._log(
            f"\tPrec: {metrics_dict['precision']:0.3f} "
            f"Rec: {metrics_dict['recall']:0.3f} "
            f"F1: {metrics_dict['f1']:0.3f}",
            level=2 - logging_offset,
        )

        # choose one metric to be used for the overall model evaluation
        if self.single_target:
            score = metrics_dict["f1"]
        else:
            score = metrics_dict["map"]

        return score, metrics_dict

    def save(self, path, save_train_loader=False):
        """save model with weights using torch.save()

        load from saved file with torch.load(path) or cnn.load_model(path)

        Args:
            path: file path for saved model object
            save_train_loader: retrain .train_loader in saved object
                [default: False]
        """
        os.makedirs(Path(path).parent, exist_ok=True)
        model_copy = copy.deepcopy(self)
        if not save_train_loader:
            try:
                delattr(model_copy, "train_loader")
            except AttributeError:
                pass
        torch.save(model_copy, path)

    def save_weights(self, path):
        """save just the weights of the network

        This allows the saved weights to be used more flexibly than model.save()
        which will pickle the entire object. The weights are saved in a pickled
        dictionary using torch.save(self.network.state_dict())

        Args:
            path: location to save weights file
        """
        torch.save(self.network.state_dict(), path)

    def load_weights(self, path, strict=True):
        """load network weights state dict from a file

        For instance, load weights saved with .save_weights()
        in-place operation

        Args:
            path: file path with saved weights
            strict: (bool) see torch.load()
        """
        self.network.load_state_dict(torch.load(path), strict=strict)

    def predict(
        self,
        samples,
        batch_size=1,
        num_workers=0,
        activation_layer=None,
        split_files_into_clips=True,
        overlap_fraction=0,
        final_clip=None,
        bypass_augmentations=True,
        invalid_samples_log=None,
        wandb_session=None,
        return_invalid_samples=False,
    ):
        """Generate predictions on a dataset

        Choose to return any combination of scores, labels, and single-target or
        multi-target binary predictions. Also choose activation layer for scores
        (softmax, sigmoid, softmax then logit, or None). Binary predictions are
        performed post-activation layer

        Note: the order of returned dataframes is (scores, preds, labels)

        Args:
            samples:
                the files to generate predictions for. Can be:
                - a dataframe with index containing audio paths, OR
                - a dataframe with multi-index (file, start_time, end_time), OR
                - a list (or np.ndarray) of audio file paths
            batch_size:
                Number of files to load simultaneously [default: 1]
            num_workers:
                parallelization (ie cpus or cores), use 0 for current process
                [default: 0]
            activation_layer:
                Optionally apply an activation layer such as sigmoid or
                softmax to the raw outputs of the model.
                options:
                - None: no activation, return raw scores (ie logit, [-inf:inf])
                - 'softmax': scores all classes sum to 1
                - 'sigmoid': all scores in [0,1] but don't sum to 1
                - 'softmax_and_logit': applies softmax first then logit
                [default: None]
            split_files_into_clips:
                If True, internally splits and predicts on clips from longer audio files
                Otherwise, assumes each row of `samples` corresponds to one complete sample
            overlap_fraction: fraction of overlap between consecutive clips when
                predicting on clips of longer audio files. For instance, 0.5
                gives 50% overlap between consecutive clips.
            final_clip: see `opensoundscape.helpers.generate_clip_times_df`
            bypass_augmentations: If False, Actions with
                is_augmentation==True are performed. Default True.
            invalid_samples_log: if not None, samples that failed to preprocess
                will be listed in this text file.
            wandb_session: a wandb session to log to
                - pass the value returned by wandb.init() to progress log to a
                Weights and Biases run
                - if None, does not log to wandb
            return_invalid_samples: bool, if True, returns second argument, a set
                containing file paths of samples that caused errors during preprocessing
                [default: False]

        Returns:
            df of post-activation_layer scores
            - if return_invalid_samples is True, returns (df,invalid_samples)
            where invalid_samples is a set of file paths that failed to preprocess

        Effects:
            (1) wandb logging
            If wandb_session is provided, logs progress and samples to Weights
            and Biases. A random set of samples is preprocessed and logged to
            a table. Progress over all batches is logged. Afte prediction,
            top scoring samples are logged.
            Use self.wandb_logging dictionary to change the number of samples
            logged or which classes have top-scoring samples logged.

            (2) unsafe sample logging
            If unsafe_samples_log is not None, saves a list of all file paths that
            failed to preprocess in unsafe_samples_log as a text file

        Note: if loading an audio file raises a PreprocessingError, the scores
            for that sample will be np.nan

        """
        assert type(samples) in (list, np.ndarray, pd.DataFrame), (
            "`samples` must be either: "
            "(a) list or np.array of files, or DataFrame with (b) file as Index or "
            "(c) (file,start_time,end_time) as MultiIndex"
        )

        # set up prediction Dataset, considering three possible cases:
        # (c1) user provided multi-index df with file,start_time,end_time of clips
        # (c2) user provided file list and wants clips to be split out automatically
        # (c3) split_files_into_clips=False -> one sample & one prediction per file provided
        if type(samples) == pd.DataFrame and type(samples.index) == MultiIndex:  # c1
            prediction_dataset = AudioFileDataset(
                samples=samples, preprocessor=self.preprocessor, return_labels=False
            )
        elif split_files_into_clips:  # c2
            prediction_dataset = AudioSplittingDataset(
                samples=samples,
                preprocessor=self.preprocessor,
                overlap_fraction=overlap_fraction,
                final_clip=final_clip,
            )
        else:  # c3
            prediction_dataset = AudioFileDataset(
                samples=samples, preprocessor=self.preprocessor, return_labels=False
            )
        prediction_dataset.bypass_augmentations = bypass_augmentations

        ## Input Validation ##
        if len(prediction_dataset.classes) > 0 and list(self.classes) != list(
            prediction_dataset.classes
        ):
            warnings.warn(
                "The columns of input samples df differ from `model.classes`."
            )

        if len(prediction_dataset) < 1:
            warnings.warn(
                "prediction_dataset has zero samples. No predictions will be generated."
            )
            return pd.DataFrame(columns=self.classes)

        # SafeDataset will not fail on bad files,
        # but will provide a different sample! Later we go back and replace scores
        # with np.nan for the bad samples (using safe_dataset._invalid_indices)
        # this approach to error handling feels hacky
        # however, returning None would break the batching of samples
        safe_dataset = SafeDataset(
            prediction_dataset, invalid_sample_behavior="substitute"
        )

        dataloader = torch.utils.data.DataLoader(
            safe_dataset,
            batch_size=batch_size,
            num_workers=num_workers,
            shuffle=False,
            # use pin_memory=True when loading files on CPU and training on GPU
            pin_memory=torch.cuda.is_available(),
        )
        # add any paths that failed to generate a clip df to _invalid_samples
        dataloader.dataset._invalid_samples = dataloader.dataset._invalid_samples.union(
            prediction_dataset.invalid_samples
        )

        # Initialize Weights and Biases (wandb) logging
        if wandb_session is not None:

            # update the run config with information about the model
            wandb_session.config.update(self._generate_wandb_config())

            # update the run config with prediction parameters
            wandb_session.config.update(
                dict(
                    batch_size=batch_size,
                    num_workers=num_workers,
                    activation_layer=activation_layer,
                )
            )

            # Log a table of preprocessed samples to wandb
            wandb_session.log(
                {
                    "Samples / Preprocessed samples": opensoundscape.wandb.wandb_table(
                        prediction_dataset,
                        self.wandb_logging["n_preview_samples"],
                    )
                }
            )

        ### Prediction/Inference ###

        # move network to device
        self.network.to(self.device)
        self.network.eval()

        # initialize scores
        total_scores = []

        # disable gradient updates during inference
        with torch.set_grad_enabled(False):

            for i, batch in enumerate(dataloader):
                # get batch of Tensors
                batch_tensors = batch["X"].to(self.device)
                batch_tensors.requires_grad = False

                # forward pass of network: feature extractor + classifier
                logits = self.network.forward(batch_tensors)

                ### Activation layer ###
                scores = apply_activation_layer(logits, activation_layer)

                # disable gradients on returned values
                total_scores.append(scores.detach().cpu().numpy())

                if wandb_session is not None:
                    wandb_session.log(
                        {
                            "progress": i / len(dataloader),
                            "completed_batches": i,
                            "total_batches": len(dataloader),
                        }
                    )

        # aggregate across all batches
        total_scores = np.concatenate(total_scores, axis=0)

        # replace scores with nan for samples that failed in preprocessing
        # this feels hacky (we predicted on substitute-samples rather than
        # skipping the samples that failed preprocessing)
        total_scores[dataloader.dataset._invalid_indices, :] = np.nan

        # return DataFrame with same index/columns as prediction_dataset's df
        df_index = prediction_dataset.label_df.index
        score_df = pd.DataFrame(index=df_index, data=total_scores, columns=self.classes)

        # warn the user if there were invalid samples (failed to preprocess)
        # and log them to a file
        invalid_samples = dataloader.dataset.report(log=invalid_samples_log)

        # log top-scoring samples per class to wandb table
        if wandb_session is not None:
            classes_to_log = self.wandb_logging["top_samples_classes"]
            if classes_to_log is None:  # pick the first few classes if none specified
                classes_to_log = self.classes
                if len(classes_to_log) > 5:  # don't accidentally log hundreds of tables
                    classes_to_log = classes_to_log[0:5]

            for i, c in enumerate(classes_to_log):
                top_samples = score_df.nlargest(
                    n=self.wandb_logging["n_top_samples"], columns=[c]
                )
                dataset = AudioFileDataset(
                    samples=top_samples,
                    preprocessor=self.preprocessor,
                    return_labels=False,
                    bypass_augmentations=True,
                )
                table = opensoundscape.wandb.wandb_table(
                    dataset=dataset,
                    classes_to_extract=[c],
                )
                wandb_session.log({f"Samples / Top scoring [{c}]": table})

        if return_invalid_samples:
            return score_df, invalid_samples
        else:
            return score_df


def use_resample_loss(model):
    """Modify a model to use ResampleLoss for multi-target training

    ResampleLoss may perform better than BCE Loss for multitarget problems
    in some scenarios.
    """

    model.loss_cls = ResampleLoss

    def _init_loss_fn(self):
        """overrides the parent method because we need to pass class frequency
        to the ResampleLoss constructor
        """
        class_frequency = (
            torch.tensor(self.train_loader.dataset.dataset.label_df.values)
            .sum(0)
            .to(self.device)
        )

        # initializing ResampleLoss requires us to pass class_frequency
        self.loss_fn = self.loss_cls(class_frequency)

    model._init_loss_fn = types.MethodType(_init_loss_fn, model)


def separate_resnet_feat_clf(model):
    """Separate feature/classifier training params for a ResNet model

    Args:
        model: an opso model object with a pytorch resnet architecture

    Returns:
        model with modified .optimizer_params and ._init_optimizer() method

    Effects:
        creates a new self.opt_net object that replaces the old one
        resets self.current_epoch to 0
    """

    # optimization parameters for parts of the networks - see
    # https://pytorch.org/docs/stable/optim.html#per-parameter-options
    model.optimizer_params = {
        "feature": {  # optimizer parameters for feature extraction layers
            # "params": self.network.feature.parameters(),
            "lr": 0.001,
            "momentum": 0.9,
            "weight_decay": 0.0005,
        },
        "classifier": {  # optimizer parameters for classification layers
            # "params": self.network.classifier.parameters(),
            "lr": 0.01,
            "momentum": 0.9,
            "weight_decay": 0.0005,
        },
    }

    # We override the parent method because we need to pass a list of
    # separate optimizer_params for different parts of the network
    # - ie we now have a dictionary of param dictionaries instead of just a
    # param dictionary.
    def _init_optimizer(self):
        """override parent method to pass separate parameters to feat/clf"""
        param_dict = self.optimizer_params
        # in torch's resnet classes, the classifier layer is called "fc"
        feature_extractor_params_list = [
            param
            for name, param in self.network.named_parameters()
            if not name.split(".")[0] == "fc"
        ]
        classifier_params_list = [
            param
            for name, param in self.network.named_parameters()
            if name.split(".")[0] == "fc"
        ]
        param_dict["feature"]["params"] = feature_extractor_params_list
        param_dict["classifier"]["params"] = classifier_params_list
        return self.optimizer_cls(param_dict.values())

    model._init_optimizer = types.MethodType(_init_optimizer, model)
    model.opt_net = None  # clears existing opt_net and its parameters
    model.current_epoch = 0  # resets the epoch to 0
    # model.opt_net will be created when .train() calls ._set_train()


class InceptionV3(CNN):
    """Child of CNN class for InceptionV3 architecture"""

    def __init__(
        self,
        classes,
        sample_duration,
        single_target=False,
        preprocessor_class=SpectrogramPreprocessor,
        freeze_feature_extractor=False,
        use_pretrained=True,
        sample_shape=[299, 299, 3],
    ):
        """Model object for InceptionV3 architecture subclassing CNN

        See opensoundscape.org for exaple use.

        Args:
            classes:
                list of output classes (usually strings)
            freeze-feature_extractor:
                if True, feature weights don't have
                gradient, and only final classification layer is trained
            use_pretrained:
                if True, use pre-trained InceptionV3 Imagenet weights
            single_target:
                if True, predict exactly one class per sample

        """

        self.classes = classes

        architecture = inception_v3(
            len(self.classes),
            freeze_feature_extractor=freeze_feature_extractor,
            use_pretrained=use_pretrained,
        )

        super(InceptionV3, self).__init__(
            architecture,
            classes,
            sample_duration,
            single_target=single_target,
            preprocessor_class=preprocessor_class,
            sample_shape=sample_shape,
        )
        self.name = "InceptionV3"

    def _train_epoch(self, train_loader, wandb_session=None):
        """perform forward pass, loss, backpropagation for one epoch

        need to override parent because Inception returns different outputs
        from the forward pass (final and auxiliary layers)

        Returns: (targets, predictions, scores) on training files
        """

        self.network.train()

        total_tgts = []
        total_preds = []
        total_scores = []
        batch_loss = []

        for batch_idx, batch_data in enumerate(train_loader):
            # load a batch of images and labels from the train loader
            # all augmentation occurs in the Preprocessor (train_loader)
            batch_tensors = batch_data["X"].to(self.device)
            batch_labels = batch_data["y"].to(self.device)
            batch_labels = batch_labels.squeeze(1)

            ####################
            # Forward and loss #
            ####################

            # forward pass: feature extractor and classifier
            # inception returns two sets of outputs
            inception_outputs = self.network.forward(batch_tensors)
            logits = inception_outputs.logits
            aux_logits = inception_outputs.aux_logits

            # save targets and predictions
            total_scores.append(logits.detach().cpu().numpy())
            total_tgts.append(batch_labels.detach().cpu().numpy())

            # generate binary predictions
            if self.single_target:  # predict highest scoring class only
                batch_preds = F.one_hot(logits.argmax(1), len(logits[0]))
            else:  # multi-target: predict 0 or 1 based on a fixed threshold
                batch_preds = torch.sigmoid(logits) >= self.prediction_threshold
            total_preds.append(batch_preds.int().detach().cpu().numpy())

            # calculate loss
            loss1 = self.loss_fn(logits, batch_labels)
            loss2 = self.loss_fn(aux_logits, batch_labels)
            loss = loss1 + 0.4 * loss2

            # save loss for each batch; later take average for epoch

            batch_loss.append(loss.detach().cpu().numpy())

            #############################
            # Backward and optimization #
            #############################
            # zero gradients for optimizer
            self.opt_net.zero_grad()
            # backward pass: calculate the gradients
            loss.backward()
            # update the network using the gradients*lr
            self.opt_net.step()

            ###########
            # Logging #
            ###########
            # log basic train info (used to print every batch)
            if batch_idx % self.log_interval == 0:
                # show some basic progress metrics during the epoch
                N = len(train_loader)
                self._log(
                    f"Epoch: {self.current_epoch} "
                    f"[batch {batch_idx}/{N}, {100 * batch_idx / N :.2f}%] "
                )

                # Log the Jaccard score and Hamming loss, and Loss function
                epoch_loss_avg = np.mean(batch_loss)
                self._log(f"\tDistLoss: {epoch_loss_avg:.3f}")

                # Evaluate with model's eval function
                tgts = batch_labels.int().detach().cpu().numpy()
                scores = logits.int().detach().cpu().numpy()
                self.eval(tgts, scores, logging_offset=-1)

            if wandb_session is not None:
                wandb_session.log({"batch": batch_idx})
                wandb_session.log(
                    {"epoch_progress": self.current_epoch + batch_idx / N}
                )

        # update learning parameters each epoch
        self.scheduler.step()

        # save the loss averaged over all batches
        self.loss_hist[self.current_epoch] = np.mean(batch_loss)

        # return targets, preds, scores
        total_tgts = np.concatenate(total_tgts, axis=0)
        total_preds = np.concatenate(total_preds, axis=0)
        total_scores = np.concatenate(total_scores, axis=0)

        return total_tgts, total_preds, total_scores


def load_model(path, device=None):
    """load a saved model object

    Args:
        path: file path of saved model
        device: which device to load into, eg 'cuda:1'
        [default: None] will choose first gpu if available, otherwise cpu

    Returns:
        a model object with loaded weights
    """
    if device is None:
        device = (
            torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")
        )
    model = torch.load(path, map_location=device)

    # since ResampleLoss class overrides a method of an instance,
    # we need to re-change the _init_loss_fn change when we reload
    if model.loss_cls == ResampleLoss:
        use_resample_loss(model)

    model.device = device
    return model


def load_outdated_model(
    path, architecture, sample_duration, model_class=CNN, device=None
):
    """load a CNN saved with a previous version of OpenSoundscape

    This function enables you to load models saved with opso 0.4.x and 0.5.x.
    If your model was saved with .save() in a previous version of OpenSoundscape
    >=0.6.0, you must re-load the model
    using the original package version and save it's network's state dict, i.e.,
    `torch.save(model.network.state_dict(),path)`, then load the state dict
    to a new model object with model.load_weights(). See the
    `Predict with pre-trained CNN` tutorial for details.

    For models created with the same version of OpenSoundscape as the one
    you are using, simply use opensoundscape.torch.models.cnn.load_model().

    Note: for future use of the loaded model, you can simply call
    `model.save(path)` after creating it, then reload it with
    `model = load_model(path)`.
    The saved model will be fully compatible with opensoundscape >=0.7.0.

    Examples:
    ```
    #load a binary resnet18 model from opso 0.4.x, 0.5.x, or 0.6.0
    from opensoundscape.torch.models.cnn import CNN
    model = load_outdated_model('old_model.tar',architecture='resnet18')

    #load a resnet50 model of class CNN created with opso 0.5.0
    from opensoundscape.torch.models.cnn import CNN
    model_050 = load_outdated_model('opso050_pytorch_model_r50.model',architecture='resnet50')
    ```

    Args:
        path: path to model file, ie .model or .tar file
        architecture: see CNN docs
            (pass None if the class __init__ does not take architecture as an argument)
        sample_duration: length of samples in seconds
        model_class: class to construct. Normally CNN.
        device: optionally specify a device to map tensors onto,
        eg 'cpu', 'cuda:0', 'cuda:1'[default: None]
            - if None, will choose cuda:0 if cuda is available, otherwise chooses cpu

    Returns:
        a cnn model object with the weights loaded from the saved model
    """
    if device is None:
        device = (
            torch.device("cuda:0") if torch.cuda.is_available() else torch.device("cpu")
        )

    try:
        # use torch to load the saved model object
        model_dict = torch.load(path, map_location=device)
    except AttributeError as exc:
        raise Exception(
            "This model could not be loaded in this version of "
            "OpenSoundscape. You may need to load the model with the version "
            "of OpenSoundscape that created it and torch.save() the "
            "model.network.state_dict(), then load the weights with model.load_weights"
        ) from exc

    if type(model_dict) != dict:
        raise ValueError(
            "This model was saved as a complete object. Try using load_model() instead."
        )

    # get the list of classes
    if "classes" in model_dict:
        classes = model_dict["classes"]
    elif "labels_yaml" in model_dict:
        classes = list(yaml.safe_load(model_dict["labels_yaml"]).values())
    else:
        raise ValueError("Could not get a list of classes from the saved model.")

    # try to construct a model object
    if architecture is None:
        model = model_class(classes=classes, sample_duration=sample_duration)
    else:
        model = model_class(
            architecture=architecture, classes=classes, sample_duration=sample_duration
        )

    # rename keys of resnet18 architecture from 0.4.x-0.6.0 to match pytorch resnet18 keys
    model_dict["model_state_dict"] = {
        k.replace("classifier.", "fc.").replace("feature.", ""): v
        for k, v in model_dict["model_state_dict"].items()
    }

    # load the state dictionary of the network, allowing mismatches
    mismatched_keys = model.network.load_state_dict(
        model_dict["model_state_dict"], strict=False
    )
    print("mismatched keys:")
    print(mismatched_keys)

    # if there's no record of single-tartet vs multitarget, it' single target from opso 0.4.x
    try:
        single_target = model_dict["single_target"]
    except KeyError:
        single_target = True

    model.single_target = single_target

    warnings.warn(
        "After loading a model, you still need to ensure that your "
        "preprocessing (model.preprocessor) matches the settings used to create"
        "the original model."
    )

    return model
