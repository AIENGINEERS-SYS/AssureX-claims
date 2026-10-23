import fs from "node:fs";
import path from "node:path";
import * as tf from "@tensorflow/tfjs";

const output = path.resolve(process.argv[2] || "gtm_model/candidate");
const model = tf.sequential();
model.add(tf.layers.conv2d({ inputShape: [64, 64, 3], filters: 8, kernelSize: 3,
  strides: 2, activation: "relu", padding: "same" }));
model.add(tf.layers.maxPooling2d({ poolSize: 2 }));
model.add(tf.layers.conv2d({ filters: 16, kernelSize: 3, activation: "relu", padding: "same" }));
model.add(tf.layers.maxPooling2d({ poolSize: 2 }));
model.add(tf.layers.conv2d({ filters: 24, kernelSize: 3, activation: "relu", padding: "same" }));
model.add(tf.layers.flatten({}));
model.add(tf.layers.dense({ units: 32, activation: "relu" }));
model.add(tf.layers.dense({ units: 3, activation: "softmax" }));
fs.mkdirSync(output, { recursive: true });
await model.save(tf.io.withSaveHandler(async artifacts => {
  fs.writeFileSync(path.join(output, "model.json"), JSON.stringify({
    format: "layers-model", generatedBy: "AssureX GTM training",
    modelTopology: artifacts.modelTopology,
    weightsManifest: [{ paths: ["weights.bin"], weights: artifacts.weightSpecs }],
  }));
  fs.writeFileSync(path.join(output, "metadata.json"), JSON.stringify({
    labels: ["valid", "invalid", "manual_review"], imageSize: 64,
  }));
  return { modelArtifactsInfo: tf.io.getModelArtifactsInfoForJSON(artifacts) };
}));
console.log("Created GTM export template:", output);
