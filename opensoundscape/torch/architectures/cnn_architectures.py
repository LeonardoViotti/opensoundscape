"""Module to initialize PyTorch CNN architectures with custom output shape

This module allows the use of several built-in CNN architectures from PyTorch.
The architecture refers to the specific layers and layer input/output shapes
(including convolution sizes and strides, etc) - such as the ResNet18 or
Inception V3 architecture.

We provide wrappers which modify the output layer to the desired shape
(to match the number of classes). The way to change the output layer
shape depends on the architecture, which is why we need a wrapper for each one.
This code is based on
pytorch.org/tutorials/beginner/finetuning_torchvision_models_tutorial.html

To use these wrappers, for example, if your
model has 10 output classes, write

`my_arch=resnet18(10)`

Then you can initialize a model object from
`opensoundscape.torch.models.cnn` with your architecture:

`model=CNN(my_arch,classes,sample_duration)`

or override an existing model's architecture:

`model.network = my_arch`

Note: the InceptionV3 architecture must be used differently than other
architectures - the easiest way is to simply use the InceptionV3 class in
opensoundscape.torch.models.cnn.
"""
from torchvision import models
from torch import nn
from opensoundscape.torch.architectures.utils import CompositeArchitecture
import torch
import torchvision
import warnings

ARCH_DICT = dict()


def register_arch(func):
    # register the model in dictionary
    ARCH_DICT[func.__name__] = func
    # return the function
    return func


def list_architectures():
    return list(ARCH_DICT.keys())


def freeze_params(model):
    """remove gradients (aka freeze) all model parameters"""
    for param in model.parameters():
        param.requires_grad = False


@register_arch
def resnet18(
    num_classes, freeze_feature_extractor=False, use_pretrained=True, num_channels=3
):
    """Wrapper for ResNet18 architecture

    input_size = 224

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo. If num_channels != 3, averages the conv1 weights across all channels.
        num_channels:
            specify channels in input sample, eg [channels h,w] sample shape
    """
    architecture_ft = models.resnet18(pretrained=use_pretrained)

    # prevent weights of feature extractor from being trained, if desired
    if freeze_feature_extractor:
        freeze_params(architecture_ft)

    # change number of output nodes
    architecture_ft.fc = change_fc_output_size(architecture_ft.fc, num_classes)

    # change input shape num_channels
    architecture_ft.conv1 = change_conv2d_channels(architecture_ft.conv1, num_channels)

    return architecture_ft


@register_arch
def resnet34(
    num_classes, freeze_feature_extractor=False, use_pretrained=True, num_channels=3
):
    """Wrapper for ResNet34 architecture

    input_size = 224

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo. If num_channels != 3, averages the conv1 weights across all channels.
        num_channels:
            specify channels in input sample, eg [channels h,w] sample shape
    """
    architecture_ft = models.resnet34(pretrained=use_pretrained)

    # prevent weights of feature extractor from being trained, if desired
    if freeze_feature_extractor:
        freeze_params(architecture_ft)

    # change number of output nodes
    architecture_ft.fc = change_fc_output_size(architecture_ft.fc, num_classes)

    # change input shape num_channels
    architecture_ft.conv1 = change_conv2d_channels(architecture_ft.conv1, num_channels)

    return architecture_ft


@register_arch
def resnet50(
    num_classes, freeze_feature_extractor=False, use_pretrained=True, num_channels=3
):
    """Wrapper for ResNet50 architecture

    input_size = 224

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo. If num_channels != 3, averages the conv1 weights across all channels.
        num_channels:
            specify channels in input sample, eg [channels h,w] sample shape
    """
    architecture_ft = models.resnet50(pretrained=use_pretrained)

    # prevent weights of feature extractor from being trained, if desired
    if freeze_feature_extractor:
        freeze_params(architecture_ft)

    # change number of output nodes
    architecture_ft.fc = change_fc_output_size(architecture_ft.fc, num_classes)

    # change input shape num_channels
    architecture_ft.conv1 = change_conv2d_channels(architecture_ft.conv1, num_channels)

    return architecture_ft


@register_arch
def resnet101(
    num_classes, freeze_feature_extractor=False, use_pretrained=True, num_channels=3
):
    """Wrapper for ResNet101 architecture

    input_size = 224

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo. If num_channels != 3, averages the conv1 weights across all channels.
        num_channels:
            specify channels in input sample, eg [channels h,w] sample shape
    """
    architecture_ft = models.resnet101(pretrained=use_pretrained)

    # prevent weights of feature extractor from being trained, if desired
    if freeze_feature_extractor:
        freeze_params(architecture_ft)

    # change number of output nodes
    architecture_ft.fc = change_fc_output_size(architecture_ft.fc, num_classes)

    # change input shape num_channels
    architecture_ft.conv1 = change_conv2d_channels(architecture_ft.conv1, num_channels)

    return architecture_ft


@register_arch
def resnet152(
    num_classes, freeze_feature_extractor=False, use_pretrained=True, num_channels=3
):
    """Wrapper for ResNet152 architecture

    input_size = 224

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo. If num_channels != 3, averages the conv1 weights across all channels.
        num_channels:
            specify channels in input sample, eg [channels h,w] sample shape
    """
    architecture_ft = models.resnet152(pretrained=use_pretrained)
    if freeze_feature_extractor:
        freeze_params(architecture_ft)

    # prevent weights of feature extractor from being trained, if desired
    if freeze_feature_extractor:
        freeze_params(architecture_ft)

    # change number of output nodes
    architecture_ft.fc = change_fc_output_size(architecture_ft.fc, num_classes)

    # change input shape num_channels
    architecture_ft.conv1 = change_conv2d_channels(architecture_ft.conv1, num_channels)

    return architecture_ft


@register_arch
def alexnet(
    num_classes, freeze_feature_extractor=False, use_pretrained=True, num_channels=3
):
    """Wrapper for AlexNet architecture

    input size = 224

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo.
        num_channels:
            specify channels in input sample, eg [channels h,w] sample shape
    """
    architecture_ft = models.alexnet(
        weights=torchvision.models.AlexNet_Weights.DEFAULT if use_pretrained else None
    )

    # prevent weights of feature extractor from being trained, if desired
    if freeze_feature_extractor:
        freeze_params(architecture_ft)

    # change number of output nodes
    architecture_ft.classifier[6] = change_fc_output_size(
        architecture_ft.classifier[6], num_classes
    )

    # change input shape num_channels
    architecture_ft.features[0] = change_conv2d_channels(
        architecture_ft.features[0], num_channels
    )

    return architecture_ft


@register_arch
def vgg11_bn(
    num_classes, freeze_feature_extractor=False, use_pretrained=True, num_channels=3
):
    """Wrapper for vgg11 architecture

    input size = 224

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo.

    """
    architecture_ft = models.vgg11_bn(pretrained=use_pretrained)

    # prevent weights of feature extractor from being trained, if desired
    if freeze_feature_extractor:
        freeze_params(architecture_ft)

    # change number of output nodes
    architecture_ft.classifier[6] = change_fc_output_size(
        architecture_ft.classifier[6], num_classes
    )

    # change input shape num_channels
    architecture_ft.features[0] = change_conv2d_channels(
        architecture_ft.features[0], num_channels
    )

    return architecture_ft


@register_arch
def squeezenet1_0(
    num_classes, freeze_feature_extractor=False, use_pretrained=True, num_channels=3
):
    """Wrapper for squeezenet architecture

    input size = 224

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo.
        num_channels:
            specify channels in input sample, eg [channels h,w] sample shape
    """
    architecture_ft = models.squeezenet1_0(pretrained=use_pretrained)

    # prevent weights of feature extractor from being trained, if desired
    if freeze_feature_extractor:
        freeze_params(architecture_ft)

    # change number of output nodes
    # uses a conv2d, not a fully connected layera
    conv2d = architecture_ft.classifier[1]  # original classifier layer
    architecture_ft.classifier[1] = torch.nn.Conv2d(
        in_channels=conv2d.in_channels,
        dilation=conv2d.dilation,
        groups=conv2d.groups,
        kernel_size=conv2d.kernel_size,
        out_channels=num_classes,
        padding=conv2d.padding,
        padding_mode=conv2d.padding_mode,
        stride=conv2d.stride,
    )

    # change input shape num_channels
    architecture_ft.features[0] = change_conv2d_channels(
        architecture_ft.features[0], num_channels
    )

    return architecture_ft


@register_arch
def densenet121(
    num_classes, freeze_feature_extractor=False, use_pretrained=True, num_channels=3
):
    """Wrapper for densenet121 architecture

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo.
        num_channels:
            specify channels in input sample, eg [channels h,w] sample shape

    """
    architecture_ft = models.densenet121(pretrained=use_pretrained)

    # prevent weights of feature extractor from being trained, if desired
    if freeze_feature_extractor:
        freeze_params(architecture_ft)

    # change number of output nodes
    architecture_ft.classifier = change_fc_output_size(
        architecture_ft.classifier, num_classes
    )

    # change input shape num_channels
    architecture_ft.features[0] = change_conv2d_channels(
        architecture_ft.features[0], num_channels
    )

    return architecture_ft


@register_arch
def inception_v3(
    num_classes, freeze_feature_extractor=False, use_pretrained=True, num_channels=3
):
    """Wrapper for Inception v3 architecture

    Input: 229x229

    WARNING: expects (299,299) sized images and has auxiliary output. See
    InceptionV3 class in `opensoundscape.torch.models.cnn` for use.

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo.
        num_channels:
            specify channels in input sample, eg [channels h,w] sample shape
    """
    architecture_ft = models.inception_v3(pretrained=use_pretrained)
    if freeze_feature_extractor:
        freeze_params(architecture_ft)
    # Handle the auxilary net
    num_ftrs = architecture_ft.AuxLogits.fc.in_features
    architecture_ft.AuxLogits.fc = nn.Linear(num_ftrs, num_classes)
    # Handle the primary net
    num_ftrs = architecture_ft.fc.in_features
    architecture_ft.fc = nn.Linear(num_ftrs, num_classes)
    if num_channels != 3:
        warnings.warn(
            "Retaining weights while reshaping Inception "
            "number of input channels is not implemented. First conv2d will "
            "have random weights."
        )
        from torchvision.models.inception import BasicConv2d

        architecture_ft.Conv2d_1a_3x3 = BasicConv2d(
            num_channels, 32, kernel_size=3, stride=2
        )
    return architecture_ft


@register_arch
def efficientnet_b0(
    num_classes, freeze_feature_extractor=False, use_pretrained=True, num_channels=3
):
    """Wrapper for efficientnet_b0 architecture

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo.
        num_channels:
            specify channels in input sample, eg [channels h,w] sample shape

    """
    architecture_ft = torch.hub.load(
        "NVIDIA/DeepLearningExamples:torchhub",
        "nvidia_efficientnet_b0",
        pretrained=use_pretrained,
    )

    # prevent weights of feature extractor from being trained, if desired
    if freeze_feature_extractor:
        freeze_params(architecture_ft)

    # change number of output nodes
    architecture_ft.classifier.fc = change_fc_output_size(
        architecture_ft.classifier.fc, num_classes
    )

    # change input shape num_channels
    architecture_ft.stem.conv = change_conv2d_channels(
        architecture_ft.stem.conv, num_channels
    )

    return architecture_ft


@register_arch
def efficientnet_b4(
    num_classes, freeze_feature_extractor=False, use_pretrained=True, num_channels=3
):
    """Wrapper for efficientnet_b4 architecture

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo.
        num_channels:
            specify channels in input sample, eg [channels h,w] sample shape

    """
    architecture_ft = torch.hub.load(
        "NVIDIA/DeepLearningExamples:torchhub",
        "nvidia_efficientnet_b4",
        pretrained=use_pretrained,
    )

    # prevent weights of feature extractor from being trained, if desired
    if freeze_feature_extractor:
        freeze_params(architecture_ft)

    # change number of output nodes
    architecture_ft.classifier.fc = change_fc_output_size(
        architecture_ft.classifier.fc, num_classes
    )

    # change input shape num_channels
    architecture_ft.stem.conv = change_conv2d_channels(
        architecture_ft.stem.conv, num_channels
    )

    return architecture_ft


@register_arch
def efficientnet_widese_b0(
    num_classes, freeze_feature_extractor=False, use_pretrained=True, num_channels=3
):
    """Wrapper for efficientnet_widese_b0 architecture

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo.
        num_channels:
            specify channels in input sample, eg [channels h,w] sample shape

    """
    architecture_ft = torch.hub.load(
        "NVIDIA/DeepLearningExamples:torchhub",
        "nvidia_efficientnet_widese_b0",
        pretrained=use_pretrained,
    )

    # prevent weights of feature extractor from being trained, if desired
    if freeze_feature_extractor:
        freeze_params(architecture_ft)

    # change number of output nodes
    architecture_ft.classifier.fc = change_fc_output_size(
        architecture_ft.classifier.fc, num_classes
    )

    # change input shape num_channels
    architecture_ft.stem.conv = change_conv2d_channels(
        architecture_ft.stem.conv, num_channels
    )

    return architecture_ft


@register_arch
def efficientnet_widese_b4(
    num_classes, freeze_feature_extractor=False, use_pretrained=True, num_channels=3
):
    """Wrapper for efficientnet_widese_b4 architecture

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo.
        num_channels:
            specify channels in input sample, eg [channels h,w] sample shape

    """
    architecture_ft = torch.hub.load(
        "NVIDIA/DeepLearningExamples:torchhub",
        "nvidia_efficientnet_widese_b4",
        pretrained=use_pretrained,
    )

    # prevent weights of feature extractor from being trained, if desired
    if freeze_feature_extractor:
        freeze_params(architecture_ft)

    # change number of output nodes
    architecture_ft.classifier.fc = change_fc_output_size(
        architecture_ft.classifier.fc, num_classes
    )

    # change input shape num_channels
    architecture_ft.stem.conv = change_conv2d_channels(
        architecture_ft.stem.conv, num_channels
    )

    return architecture_ft


# @register_arch
# def efficientnet(
#     num_classes,
#     freeze_feature_extractor=False,
#     use_pretrained=True,
#     num_channels=3,
#     torchhub_entrypoint="nvidia_efficientnet_b4",
# ):
#     """Wrapper for EfficientNet architecture

#     Args:
#         num_classes:
#             number of output nodes for the final layer
#         freeze_feature_extractor:
#             if False (default), entire network will have gradients and can train
#             if True, feature block is frozen and only final layer is trained
#         use_pretrained:
#             if True, uses pre-trained ImageNet features from
#             Pytorch's model zoo.
#         num_channels:
#             specify channels in input sample, eg [channels h,w] sample shape
#         torchhub_entrypoint:
#             Choose torchhub architecture from ['nvidia_efficientnet_b0',
#             'nvidia_efficientnet_b4','nvidia_efficientnet_widese_b0',
#             'nvidia_efficientnet_widese_b4']. [default: nvidia_efficientnet_b4]
#     """
#     architecture_ft = torch.hub.load(
#         "NVIDIA/DeepLearningExamples:torchhub",
#         torchhub_entrypoint,
#         pretrained=use_pretrained,
#     )
#     if freeze_feature_extractor:
#         freeze_params(architecture_ft)
#     num_ftrs = architecture_ft.classifier.fc.in_features
#     architecture_ft.classifier = nn.Linear(num_ftrs, num_classes)
#     # change input shape num_channels
#     if num_channels != 3:
#         architecture_ft.features[0] = nn.Conv2d(
#             num_channels, 64, kernel_size=7, stride=2, padding=3, bias=False
#         )
#     return architecture_ft


def change_conv2d_channels(
    conv2d,
    num_channels=3,
    reuse_weights=True,
):
    """Modify the number of input channels for a pytorch CNN

    This function changes the input shape of a torch.nn.Conv2D layer
    to accommodate a different number of channels. It attempts to retain
    weights in the following manner:
    -  If num_channels is less than the original,
    it will average weights across the original channels and apply them
    to all new channels.
    - if num_channels is greater than the original,
    it will cycle through the original channels, copying them to the
    new channels

    Args:
        num_classes:
            number of output nodes for the final layer
        freeze_feature_extractor:
            if False (default), entire network will have gradients and can train
            if True, feature block is frozen and only final layer is trained
        use_pretrained:
            if True, uses pre-trained ImageNet features from
            Pytorch's model zoo.
        num_channels:
            specify channels in input sample, eg [channels h,w] sample shape
        reuse_weights: if True (default), averages (if num_channels<original)
        or cycles through (if num_channels>original) original channel weights
            and adds them to the new Conv2D
    """
    # change input shape num_channels
    if num_channels == conv2d.in_channels:
        return conv2d  # already correct shape, don't modify

    # modify the input layer to accept custom # channels
    new_conv2d = torch.nn.Conv2d(
        in_channels=num_channels,
        dilation=conv2d.dilation,
        groups=conv2d.groups,
        kernel_size=conv2d.kernel_size,
        out_channels=conv2d.out_channels,
        padding=conv2d.padding,
        padding_mode=conv2d.padding_mode,
        stride=conv2d.stride,
    )

    if reuse_weights:
        # use weights from the original model
        if num_channels < conv2d.in_channels:
            # apply weights averaged across channels to each new channel
            weights = torch.repeat_interleave(
                torch.unsqueeze(torch.mean(conv2d.weight, 1), 1),
                num_channels,
                1,  # dimension to average on; 0 is feats, 1 is channels
            )
        else:
            # cycle through original channels, copying weights to new channels
            new_channels = [
                conv2d.weight[:, i % conv2d.in_channels, :, :]
                for i in range(num_channels)
            ]
            weights = torch.stack(new_channels, dim=1)  # stack on channel dim, dim 1
        # reapply the average weights of the original architecture's conv1
        new_conv2d.weight = torch.nn.Parameter(weights)

    return new_conv2d


def change_fc_output_size(
    fc,
    num_classes,
):
    """Modify the number of output nodes of a fully connected layer

    Args:
        fc: the fully connected layer of the model that should
            be modified
        num_classes:
            number of output nodes for the new fc
    """
    num_ftrs = fc.in_features
    return nn.Linear(num_ftrs, num_classes)
