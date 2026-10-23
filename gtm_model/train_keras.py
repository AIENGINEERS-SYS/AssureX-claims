"""One-off CPU training; deploy only the exported TensorFlow.js artifacts."""
import argparse
import json
from pathlib import Path

import numpy as np
import tensorflow as tf


LABELS = ("valid", "invalid", "manual_review")


def dataset(root, split, *, shuffle):
    paths = []
    classes = []
    for index, label in enumerate(LABELS):
        for image in sorted((root / split / label).glob("*.png")):
            paths.append(str(image))
            classes.append(index)
    if not paths:
        raise ValueError(f"No {split} images in {root}")
    data = tf.data.Dataset.from_tensor_slices((paths, classes))
    if shuffle:
        data = data.shuffle(len(paths), seed=104729, reshuffle_each_iteration=True)

    def decode(path, label):
        image = tf.io.decode_png(tf.io.read_file(path), channels=3)
        # Match runtime.mjs: tfjs resizeBilinear(..., alignCorners=true).
        image = tf.raw_ops.ResizeBilinear(
            images=tf.expand_dims(tf.cast(image, tf.float32), 0),
            size=[64, 64], align_corners=True, half_pixel_centers=False,
        )[0]
        return tf.cast(image, tf.float32) / 127.5 - 1.0, tf.one_hot(label, 3)

    return data.map(decode, num_parallel_calls=tf.data.AUTOTUNE).batch(32).prefetch(tf.data.AUTOTUNE)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=Path("dataset_generator/gtm_dataset"))
    parser.add_argument("--output", type=Path, default=Path("gtm_model/candidate"))
    parser.add_argument("--epochs", type=int, default=8)
    args = parser.parse_args()
    tf.keras.utils.set_random_seed(104729)
    tf.config.threading.set_intra_op_parallelism_threads(4)
    train = dataset(args.dataset, "train", shuffle=True)
    validation = dataset(args.dataset, "validation", shuffle=False)
    model = tf.keras.Sequential([
        tf.keras.layers.Input((64, 64, 3)),
        tf.keras.layers.Conv2D(8, 3, strides=2, padding="same", activation="relu"),
        tf.keras.layers.MaxPooling2D(2),
        tf.keras.layers.Conv2D(16, 3, padding="same", activation="relu"),
        tf.keras.layers.MaxPooling2D(2),
        tf.keras.layers.Conv2D(24, 3, padding="same", activation="relu"),
        tf.keras.layers.Flatten(),
        tf.keras.layers.Dense(32, activation="relu"),
        tf.keras.layers.Dense(3, activation="softmax"),
    ])
    model.compile(optimizer=tf.keras.optimizers.Adam(0.001), loss="categorical_crossentropy",
                  metrics=["accuracy"])
    stop = tf.keras.callbacks.EarlyStopping(monitor="val_accuracy", mode="max", patience=3,
                                            restore_best_weights=True)
    history = model.fit(train, validation_data=validation, epochs=args.epochs,
                        callbacks=[stop], verbose=2)
    manifest = json.loads((args.output / "model.json").read_text(encoding="utf-8"))
    specs = manifest["weightsManifest"][0]["weights"]
    weights = model.get_weights()
    if len(specs) != len(weights):
        raise ValueError(f"Export contract mismatch: {len(specs)} specs, {len(weights)} arrays")
    for spec, value in zip(specs, weights):
        if tuple(spec["shape"]) != value.shape:
            raise ValueError(f"Weight shape mismatch for {spec['name']}: {spec['shape']} != {value.shape}")
    with (args.output / "weights.bin").open("wb") as stream:
        for value in weights:
            stream.write(np.asarray(value, dtype="<f4").tobytes(order="C"))
    print(json.dumps({"best_validation_accuracy": max(history.history["val_accuracy"]),
                      "epochs": len(history.history["val_accuracy"]),
                      "output": str(args.output)}))


if __name__ == "__main__":
    main()
