#!/usr/bin/env node
"use strict";
import fs from "node:fs";
import path from "node:path";
import * as tf from "@tensorflow/tfjs";
import { imageTensor } from "./runtime.mjs";

const labels = ["valid", "invalid", "manual_review"];
const root = path.resolve(process.argv[2] || "dataset_generator/gtm_dataset");
const output = path.resolve(process.argv[3] || "gtm_model/candidate");
const size = 64;
const epochs = Number(process.argv[4] || 5);
const batchSize = 32;

function files(split) {
  return labels.flatMap((label, index) => {
    const folder = path.join(root, split, label);
    return fs.readdirSync(folder).filter(name => name.endsWith(".png"))
      .map(name => ({ image: path.join(folder, name), label: index }));
  });
}

const train = files("train");
const validation = files("validation");
if (!train.length || !validation.length) throw new Error("Training and validation cards are required.");

const model = tf.sequential();
model.add(tf.layers.conv2d({ inputShape: [size, size, 3], filters: 8, kernelSize: 3,
  strides: 2, activation: "relu", padding: "same" }));
model.add(tf.layers.maxPooling2d({ poolSize: 2 }));
model.add(tf.layers.conv2d({ filters: 16, kernelSize: 3, activation: "relu", padding: "same" }));
model.add(tf.layers.maxPooling2d({ poolSize: 2 }));
model.add(tf.layers.conv2d({ filters: 24, kernelSize: 3, activation: "relu", padding: "same" }));
model.add(tf.layers.flatten({}));
model.add(tf.layers.dense({ units: 32, activation: "relu" }));
model.add(tf.layers.dense({ units: 3, activation: "softmax" }));
model.compile({ optimizer: tf.train.adam(0.001), loss: "categoricalCrossentropy", metrics: ["accuracy"] });

function shuffle(rows, seed) {
  let state = seed >>> 0;
  const next = () => { state = (1664525 * state + 1013904223) >>> 0; return state / 4294967296; };
  for (let i = rows.length - 1; i > 0; i--) {
    const j = Math.floor(next() * (i + 1));
    [rows[i], rows[j]] = [rows[j], rows[i]];
  }
}

async function accuracy(rows) {
  let correct = 0;
  for (const row of rows) {
    const input = imageTensor(row.image, size);
    const output = model.predict(input);
    const values = await output.data();
    if (values.indexOf(Math.max(...values)) === row.label) correct++;
    input.dispose(); output.dispose();
  }
  return correct / rows.length;
}

async function save() {
  fs.mkdirSync(output, { recursive: true });
  await model.save(tf.io.withSaveHandler(async artifacts => {
    const weights = Buffer.from(artifacts.weightData);
    fs.writeFileSync(path.join(output, "weights.bin"), weights);
    fs.writeFileSync(path.join(output, "model.json"), JSON.stringify({
      format: "layers-model", generatedBy: "AssureX tfjs training",
      modelTopology: artifacts.modelTopology,
      weightsManifest: [{ paths: ["weights.bin"], weights: artifacts.weightSpecs }],
    }));
    fs.writeFileSync(path.join(output, "metadata.json"), JSON.stringify({ labels, imageSize: size }));
    return { modelArtifactsInfo: tf.io.getModelArtifactsInfoForJSON(artifacts) };
  }));
}

let best = 0;
for (let epoch = 1; epoch <= epochs; epoch++) {
  shuffle(train, 104729 + epoch);
  let batchLoss = 0;
  for (let offset = 0; offset < train.length; offset += batchSize) {
    const rows = train.slice(offset, offset + batchSize);
    const inputs = rows.map(row => imageTensor(row.image, size));
    const x = tf.concat(inputs, 0);
    inputs.forEach(item => item.dispose());
    const indices = tf.tensor1d(rows.map(row => row.label), "int32");
    const y = tf.oneHot(indices, 3);
    indices.dispose();
    const metrics = await model.trainOnBatch(x, y);
    batchLoss += Number(Array.isArray(metrics) ? metrics[0] : metrics);
    x.dispose(); y.dispose();
    if (offset && offset % (batchSize * 25) === 0) console.log(JSON.stringify({ epoch, trained: offset, total: train.length }));
  }
  const score = await accuracy(validation);
  console.log(JSON.stringify({ epoch, loss: +(batchLoss / Math.ceil(train.length / batchSize)).toFixed(4),
    validation_accuracy: +score.toFixed(4) }));
  if (score > best) { best = score; await save(); }
}
console.log(JSON.stringify({ best_validation_accuracy: best, output }));
