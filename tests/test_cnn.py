from opensoundscape.preprocess.preprocessors import (
    CnnPreprocessor,
    LongAudioPreprocessor,
)
from opensoundscape.torch.models.cnn import (
    PytorchModel,
    Resnet18Multiclass,
    Resnet18Binary,
    InceptionV3,
)
from opensoundscape.torch.architectures.cnn_architectures import alexnet
import pandas as pd
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import shutil


@pytest.fixture()
def model_save_path(request):
    path = Path("tests/models/temp.model")

    # always delete this at the end
    def fin():
        path.unlink()

    request.addfinalizer(fin)

    return path


@pytest.fixture()
def train_dataset():
    df = pd.DataFrame(
        index=["tests/audio/great_plains_toad.wav", "tests/audio/1min.wav"],
        data=[[0, 1], [1, 0]],
        columns=["negative", "positive"],
    )
    return CnnPreprocessor(df, overlay_df=None)


@pytest.fixture()
def long_audio_dataset():
    df = pd.DataFrame(index=["tests/audio/1min.wav"])
    return LongAudioPreprocessor(
        df, audio_length=5.0, clip_overlap=0.0, out_shape=[224, 224]
    )


@pytest.fixture()
def test_dataset():
    df = pd.DataFrame(
        index=["tests/audio/great_plains_toad.wav", "tests/audio/1min.wav"]
    )
    return CnnPreprocessor(df, overlay_df=None, return_labels=False)


def test_multiclass_object_init():
    _ = Resnet18Multiclass([0, 1, 2, 3])


def test_train(train_dataset):
    binary = Resnet18Binary(classes=["negative", "positive"])
    binary.train(
        train_dataset,
        train_dataset,
        save_path="tests/models/binary",
        epochs=1,
        batch_size=2,
        save_interval=10,
        num_workers=0,
    )
    model_path = Path("tests/models/binary/best.model")
    binary.save(model_path)
    assert model_path.exists()

    # check that from_checkpoint works
    Resnet18Multiclass.from_checkpoint(model_path)

    shutil.rmtree("tests/models/binary")


def test_train_multiclass(train_dataset):
    model = Resnet18Multiclass(["negative", "positive"])
    model.train(
        train_dataset,
        train_dataset,
        save_path="tests/models/multiclass",
        epochs=1,
        batch_size=2,
        save_interval=10,
        num_workers=0,
    )
    model_path = Path("tests/models/multiclass/best.model")
    model.save(model_path)
    assert model_path.exists()

    # check that from_checkpoint works
    Resnet18Multiclass.from_checkpoint(model_path)

    shutil.rmtree("tests/models/multiclass/")


def test_single_target_prediction(train_dataset):
    binary = Resnet18Binary(classes=["negative", "positive"])
    _, preds, _ = binary.predict(train_dataset, binary_preds="single_target")
    assert np.sum(preds.iloc[0].values) == 1


def test_multi_target_prediction(train_dataset, test_dataset):
    binary = Resnet18Binary(classes=["negative", "positive"])
    _, preds, _ = binary.predict(
        test_dataset, binary_preds="multi_target", threshold=0.1
    )
    _, preds, _ = binary.predict(
        train_dataset, binary_preds="multi_target", threshold=0.1
    )
    assert int(np.sum(preds.iloc[0].values)) == 2


def test_train_predict_inception(train_dataset):
    model = InceptionV3(["negative", "positive"], use_pretrained=False)
    train_dataset_inception = train_dataset.sample(frac=1)
    # Inception expects input shape=(299,299)
    train_dataset_inception.actions.to_img.set(shape=[299, 299])
    model.train(
        train_dataset_inception,
        train_dataset_inception,
        save_path="tests/models/multiclass",
        epochs=1,
        batch_size=2,
        save_interval=10,
        num_workers=0,
    )
    model.predict(train_dataset, num_workers=0)
    model_path = Path("tests/models/multiclass/best.model")
    model.save(model_path)
    assert model_path.exists()

    InceptionV3.from_checkpoint(model_path)
    shutil.rmtree("tests/models/multiclass/")


def test_train_predict_architecture(train_dataset):
    """test passing a specific architecture to PytorchModel"""
    arch = alexnet(2, use_pretrained=False)
    model = PytorchModel(arch, ["negative", "positive"])
    model.train(
        train_dataset,
        train_dataset,
        save_path="tests/models/multiclass",
        epochs=1,
        batch_size=2,
        save_interval=10,
        num_workers=0,
    )
    model.predict(train_dataset, num_workers=0)
    model_path = Path("tests/models/multiclass/best.model")
    model.save(model_path)
    assert model_path.exists()
    shutil.rmtree("tests/models/multiclass/")


def test_split_and_predict(long_audio_dataset):
    binary = Resnet18Binary(classes=["negative", "positive"])
    scores, preds, _ = binary.split_and_predict(
        long_audio_dataset, binary_preds="single_target"
    )
    assert len(scores) == 12
    assert len(preds) == 12


def test_save_and_load(model_save_path):
    arch = alexnet(2, use_pretrained=False)
    m = PytorchModel(arch, classes=["negative", "positive"])
    m.save(model_save_path)
    m.load(model_save_path)


def test_Resnet18Binary_from_checkpoint(model_save_path):
    arch = alexnet(2, use_pretrained=False)
    classes = ["negative", "positive"]
    m = Resnet18Binary(classes=classes, use_pretrained=False)
    m.save(model_save_path)
    m = Resnet18Binary.from_checkpoint(model_save_path)
    assert m.classes == classes


def test_Resnet18Multiclass_from_checkpoint(model_save_path):
    classes = ["negative", "positive"]
    Resnet18Multiclass(classes=classes, use_pretrained=False).save(model_save_path)
    m = Resnet18Multiclass.from_checkpoint(model_save_path)
    assert m.classes == classes


def test_InceptionV3_from_checkpoint(model_save_path):
    classes = ["negative", "positive"]
    InceptionV3(classes=classes, use_pretrained=False).save(model_save_path)
    m = InceptionV3.from_checkpoint(model_save_path)
    assert m.classes == classes
