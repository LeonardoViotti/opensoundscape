#!/usr/bin/env python
import sklearn.metrics as M
import pandas as pd

# from scipy.sparse import csr_matrix
import numpy as np
import torch


def predict_single_target_labels(scores):
    """Generate boolean single target predicted labels from continuous scores

    For each row, the single highest scoring class will be labeled 1 and
    all other classes will be labeled 0.

    This function internally uses torch.Tensors to optimize performance

    Args:
        scores: 2d np.array, 2d list, 2d torch.Tensor, or pd.DataFrame
            containing continuous scores

    Returns: boolean value where each row has 1 for the highest scoring class and
    0 for all other classes. Returns same datatype as input.

    See also: predict_multi_target_labels

    """
    # allow 2d arrays / numpy arrays or pd.dataframe
    return_type = None
    df = None

    if isinstance(scores, pd.DataFrame):
        df = scores
        scores = torch.Tensor(df.values)
        return_type = "pandas"
    elif isinstance(scores, np.ndarray):
        scores = torch.Tensor(scores)
        return_type = "numpy"
    elif isinstance(scores, list):
        scores = torch.Tensor(scores)
        return_type = "list"
    elif isinstance(scores, torch.Tensor):
        return_type = "torch"
    else:
        raise ValueError(
            f"Expected input type numpy.ndarray, torch.tensor, list, "
            f"or pandas.DataFrame. Got {type(scores)}."
        )

    preds = torch.nn.functional.one_hot(scores.argmax(1), len(scores[0]))

    if return_type == "pandas":
        return pd.DataFrame(preds.numpy(), index=df.index, columns=df.columns)
    elif return_type == "numpy":
        return preds.numpy()
    elif return_type == "list":
        return preds.tolist()
    else:
        return preds


def predict_multi_target_labels(scores, threshold):
    """Generate boolean multi-target predicted labels from continuous scores

    For each sample, each class score is compared to a threshold. Any
    class can be predicted 1 or 0, independent of other
    classes.

    This function internally uses torch.Tensors to optimize performance

    Note: threshold can be a single value or list of per-class thresholds

    Args:
        scores: 2d np.array, 2d list, 2d torch.Tensor, or pd.DataFrame
            containing continuous scores
        threshold: a number or list of numbers with a threshold for each class
            - if a single number, used as a threshold for all classes (columns)
            - if a list, length should match number of columns in scores. Each
                value in the list will be used as a threshold for each respective
                class (column).

    Returns: 1/0 values with 1 if score exceeded threshold and 0 otherwise

    See also: predict_single_target_labels
    """
    # allow 2d arrays / numpy arrays or pd.dataframe
    return_type = None
    df = None

    if isinstance(scores, pd.DataFrame):
        df = scores
        scores = torch.Tensor(df.values)
        return_type = "pandas"
    elif isinstance(scores, np.ndarray):
        scores = torch.Tensor(scores)
        return_type = "numpy"
    elif isinstance(scores, list):
        scores = torch.Tensor(scores)
        return_type = "list"
    elif isinstance(scores, torch.Tensor):
        return_type = "torch"
    else:
        raise ValueError(
            f"Expected input type numpy.ndarray, torch.Tensor, list, "
            f"or pandas.DataFrame. Got {type(scores)}."
        )
    # will make predictions for either a single threshold value
    # or list of class-specific threshold values
    # the threshold should either be a list of numbers or a single number
    if type(threshold) in [np.array, list, torch.Tensor, tuple]:
        assert len(threshold) == 1 or len(threshold) == len(scores[0]), (
            "threshold must be a single value, or have "
            "the same number of values as there are classes"
        )
        threshold = torch.Tensor(threshold)
    elif not type(threshold) in [float, np.float32, np.float64, int]:
        raise ValueError(
            f"threshold must be a single number or "
            f"a list/torch.Tensor/tuple/np.array of numbers with one "
            f"threshold per class. Got type {type(threshold)}"
        )

    # predict 0/1 based on a fixed threshold or per-class threshold
    preds = (scores >= threshold).int()

    if return_type == "pandas":
        return pd.DataFrame(preds.numpy(), index=df.index, columns=df.columns)
    elif return_type == "numpy":
        return preds.numpy()
    elif return_type == "list":
        return preds.tolist()
    else:
        return preds


def multi_target_metrics(targets, scores, class_names, threshold):
    """generate various metrics for a set of scores and labels (targets)

    Args:
        targets: 0/1 lables in 2d array
        scores: continuous values in 2d array
        class_names: list of strings
        threshold: scores >= threshold result in prediction of 1,
            while scores < threshold result in prediction of 0

    Returns:
        metrics_dict: dictionary of various overall and per-class metrics
    """
    metrics_dict = {}

    preds = predict_multi_target_labels(scores=scores, threshold=threshold)

    # Store per-class precision, recall, and f1
    class_pre, class_rec, class_f1, _ = M.precision_recall_fscore_support(
        targets, preds, average=None, zero_division=0
    )

    for i, class_i in enumerate(class_names):
        metrics_dict.update(
            {
                class_i: {
                    "au_roc": M.roc_auc_score(targets[:, i], scores[:, i]),
                    "avg_precision": M.average_precision_score(
                        targets[:, i], scores[:, i]
                    ),
                    "precision": class_pre[i],
                    "recall": class_rec[i],
                    "f1": class_f1[i],
                    "support": np.sum(targets[:, i]),
                }
            }
        )

    # macro scores are averaged across classes
    metrics_dict["precision"] = class_pre.mean()
    metrics_dict["recall"] = class_rec.mean()
    metrics_dict["f1"] = class_f1.mean()

    try:
        metrics_dict["jaccard"] = M.jaccard_score(targets, preds, average="macro")
    except ValueError:
        metrics_dict["jaccard"] = np.nan
    try:
        metrics_dict["hamming_loss"] = M.hamming_loss(targets, preds)
    except ValueError:
        metrics_dict["hamming_loss"] = np.nan
    try:
        metrics_dict["map"] = M.average_precision_score(
            targets, scores, average="macro"
        )
    except ValueError:
        metrics_dict["map"] = np.nan
    try:
        metrics_dict["au_roc"] = M.roc_auc_score(targets, scores, average="macro")
    except ValueError:
        metrics_dict["au_roc"] = np.nan

    return metrics_dict


def single_target_metrics(targets, scores):
    """generate various metrics for a set of scores and labels (targets)

    Predicts 1 for the highest scoring class per sample and 0 for
    all other classes.

    Args:
        targets: 0/1 lables in 2d array
        scores: continuous values in 2d array

    Returns:
        metrics_dict: dictionary of various overall and per-class metrics

    """
    if max(np.sum(targets, 1)) > 1:
        raise ValueError(
            "Labels were not single target! "
            "Use multi-target classifier if multiple classes can be present "
            "in a single sample."
        )

    metrics_dict = {}

    preds = predict_single_target_labels(scores=scores)

    # Confusion matrix requires numbered-class not one-hot labels
    t = np.argmax(targets, 1)
    p = np.argmax(preds, 1)
    metrics_dict["confusion_matrix"] = M.confusion_matrix(t, p)

    # precision, recall, and f1
    pre, rec, f1, _ = M.precision_recall_fscore_support(
        targets, preds, average=None, zero_division=0
    )
    metrics_dict.update({"precision": pre[1], "recall": rec[1], "f1": f1[1]})

    try:
        metrics_dict["jaccard"] = M.jaccard_score(targets, preds, average="macro")
    except ValueError:
        metrics_dict["jaccard"] = np.nan
    try:
        metrics_dict["hamming_loss"] = M.hamming_loss(targets, preds)
    except ValueError:
        metrics_dict["hamming_loss"] = np.nan

    return metrics_dict
