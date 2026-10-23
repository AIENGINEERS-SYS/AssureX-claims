#!/usr/bin/env node
import fs from "node:fs";
import readline from "node:readline";
import { pathToFileURL } from "node:url";
import { loadModel, imageTensor } from "./runtime.mjs";

const modelPath = process.argv[2];
if (!modelPath) throw new Error("GTM model path is required");
const model = await loadModel(modelPath);
const metadata = JSON.parse(fs.readFileSync(new URL("./metadata.json", pathToFileURL(modelPath)), "utf8"));
const size = Number(metadata.imageSize) || 224;
process.stdout.write(JSON.stringify({ ready: true }) + "\n");

for await (const line of readline.createInterface({ input: process.stdin })) {
  let input;
  let output;
  try {
    const { imagePath } = JSON.parse(line);
    input = imageTensor(imagePath, size);
    output = model.predict(input);
    const prediction = Array.isArray(output) ? output[0] : output;
    process.stdout.write(JSON.stringify({ scores: Array.from(await prediction.data()) }) + "\n");
  } catch (error) {
    process.stdout.write(JSON.stringify({ error: String(error.message || error) }) + "\n");
  } finally {
    input?.dispose();
    if (Array.isArray(output)) output.forEach(item => item.dispose());
    else output?.dispose();
  }
}
