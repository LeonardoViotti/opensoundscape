"""Utilties for .ml"""
import warnings
import pandas as pd
import numpy as np
import torch
import torch.nn.functional as F
import pytorch_grad_cam
import tqdm

from opensoundscape.ml.sampling import ClassAwareSampler


class BaseModule(torch.nn.Module):
    """
    Base class for a pytorch model pipeline class.

    All child classes should define load, save, etc
    """

    name = None

    def setup_net(self):
        pass

    def setup_critera(self):
        pass

    def save(self, out_path):
        pass

    def update_best(self):
        pass


def cas_dataloader(dataset, batch_size, num_workers):
    """
    Return a dataloader that uses the class aware sampler

    Class aware sampler tries to balance the examples per class in each batch.
    It selects just a few classes to be present in each batch, then samples
    those classes for even representation in the batch.

    Args:
        dataset: a pytorch dataset type object
        batch_size: see DataLoader
        num_workers: see DataLoader
    """

    if len(dataset) == 0:
        return None

    print("** USING CAS SAMPLER!! **")

    # check that the data is single-target
    max_labels_per_file = max(dataset.df.values.sum(1))
    min_labels_per_file = min(dataset.df.values.sum(1))
    assert (
        max_labels_per_file <= 1
    ), "Class Aware Sampler for multi-target labels is not implemented. Use single-target labels."
    assert (
        min_labels_per_file > 0
    ), "Class Aware Sampler requires that every sample have a label. Some samples had 0 labels."

    # we need to convert one-hot labels to digit labels for the CAS
    # first class name -> 0, next class name -> 1, etc
    digit_labels = dataset.df.values.argmax(1)

    # create the class aware sampler object and DataLoader
    sampler = ClassAwareSampler(digit_labels, num_samples_cls=2)

    loader = torch.utils.data.DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=False,  # don't shuffle bc CAS does its own sampling
        num_workers=num_workers,
        pin_memory=True,
        sampler=sampler,
    )

    return loader


def get_batch(array, batch_size, batch_number):
    """get a single slice of a larger array

    using the batch size and batch index, from zero

    Args:
        array: iterable to split into batches
        batch_size: num elements per batch
        batch_number: index of batch
    Returns:
        one batch (subset of array)

    Note: the final elements are returned as the last batch
    even if there are fewer than batch_size

    Example:
        if array=[1,2,3,4,5,6,7] then:

        - get_batch(array,3,0) returns [1,2,3]

        - get_batch(array,3,3) returns [7]
    """
    start_idx = batch_number * batch_size
    end_idx = min((batch_number + 1) * batch_size, len(array))
    return array[start_idx:end_idx]


def apply_activation_layer(x, activation_layer=None):
    """applies an activation layer to a set of scores

    Args:
        x: input values
        activation_layer:
            - None [default]: return original values
            - 'softmax': apply softmax activation
            - 'sigmoid': apply sigmoid activation
            - 'softmax_and_logit': apply softmax then logit transform
    Returns:
        values with activation layer applied
        Note: if x is None, returns None
    """
    if x is None:
        return None

    x = torch.tensor(x)
    if activation_layer is None:  # scores [-inf,inf]
        pass
    elif activation_layer == "softmax":
        # "softmax" activation: preds across all classes sum to 1
        x = F.softmax(x, dim=1)
    elif activation_layer == "sigmoid":
        # map [-inf,inf] to [0,1]
        x = torch.sigmoid(x)
    elif activation_layer == "softmax_and_logit":
        # softmax, then remap scores from [0,1] to [-inf,inf]
        try:
            x = torch.logit(F.softmax(x, dim=1))
        except NotImplementedError:
            # use cpu because mps aten::logit is not implemented yet
            warnings.warn("falling back to CPU for logit operation")
            original_device = x.device
            x = torch.logit(F.softmax(x, dim=1).cpu()).to(original_device)

    else:
        raise ValueError(f"invalid option for activation_layer: {activation_layer}")

    return x


# override pytorch_grad_cam's score cam class because it has a bug
# with device mismatch of upsampled (cpu) vs input_tensor (may be mps, cuda, etc)
class ScoreCAM(pytorch_grad_cam.base_cam.BaseCAM):
    def __init__(self, model, target_layers, use_cuda=False, reshape_transform=None):
        super(ScoreCAM, self).__init__(
            model,
            target_layers,
            use_cuda,
            reshape_transform=reshape_transform,
            uses_gradients=False,
        )

    def get_cam_weights(self, input_tensor, target_layer, targets, activations, grads):
        with torch.no_grad():
            upsample = torch.nn.UpsamplingBilinear2d(size=input_tensor.shape[-2:])
            activation_tensor = torch.from_numpy(activations)
            if self.cuda:
                activation_tensor = activation_tensor.cuda()

            upsampled = upsample(activation_tensor)

            maxs = upsampled.view(upsampled.size(0), upsampled.size(1), -1).max(dim=-1)[
                0
            ]
            mins = upsampled.view(upsampled.size(0), upsampled.size(1), -1).min(dim=-1)[
                0
            ]

            maxs, mins = maxs[:, :, None, None], mins[:, :, None, None]
            upsampled = (upsampled - mins) / (maxs - mins)

            upsampled = upsampled.to(input_tensor.device)
            input_tensors = input_tensor[:, None, :, :] * upsampled[:, :, None, :, :]

            if hasattr(self, "batch_size"):
                BATCH_SIZE = self.batch_size
            else:
                BATCH_SIZE = 16

            scores = []
            for target, tensor in zip(targets, input_tensors):
                for i in tqdm.tqdm(range(0, tensor.size(0), BATCH_SIZE)):
                    batch = tensor[i : i + BATCH_SIZE, :]
                    outputs = [target(o).cpu().item() for o in self.model(batch)]
                    scores.extend(outputs)
            scores = torch.Tensor(scores)
            scores = scores.view(activations.shape[0], activations.shape[1])
            weights = torch.nn.Softmax(dim=-1)(scores).numpy()
            return weights
