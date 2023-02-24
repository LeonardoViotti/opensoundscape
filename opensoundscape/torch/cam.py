""" Class activation maps (CAM) for OpenSoundscape models"""
import matplotlib.pyplot as plt


class ActivationMap:
    """Base class for activation maps
    This is a container for the activation map of a given class for a given input.
    """

    def __init__(self, base_image, activation_map, class_name):
        self.base_image = base_image
        self.activation_map = activation_map
        self.activation_map = self.activation_map.squeeze(0)
        self.class_name = class_name

    def plot(self, alpha=0.5, cmap="jet", interpolation="bilinear", figsize=(10, 10)):
        """Plot the activation map
        Args:
            alpha: opacity of the activation map overlap
            cmap: the colormap for the activation map
            interpolation: the interpolation method for the activation map
            figsize: the figure size for the plot
        """
        fig, ax = plt.subplots(figsize=figsize)

        # remove the first (batch) dimension
        # move the first dimension (Nchannels) to last dimension for imshow
        # make negative
        base_image = -self.base_image.squeeze(0).permute(1, 2, 0)
        ax.imshow(base_image, alpha=1)
        ax.imshow(
            self.activation_map, cmap=cmap, alpha=alpha, interpolation=interpolation
        )
        ax.set_title(f"Activation Map for {self.class_name}")
        ax.axis("off")
        plt.show()

    def save(self, path):
        """Save the activation map to a file
        Args:
            path: the path to save the activation map to
        """
        fig, ax = plt.subplots()
        ax.imshow(self.base_image, alpha=alpha)
        ax.imshow(
            self.activation_map, cmap=cmap, alpha=alpha, interpolation=interpolation
        )
        ax.set_title(f"Activation Map for {self.class_name}")
        ax.axis("off")
        fig.savefig(path)
        plt.close(fig)

    def __repr__(self):
        return f"ActivationMap({self.class_name})"
