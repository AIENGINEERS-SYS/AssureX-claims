import fs from "node:fs";
import path from "node:path";
import * as tf from "@tensorflow/tfjs";
import { PNG } from "pngjs";
import jpeg from "jpeg-js";

export { tf };

export async function loadModel(modelPath) {
  const manifest = JSON.parse(fs.readFileSync(modelPath, "utf8"));
  const folder = path.dirname(modelPath);
  const specs = manifest.weightsManifest.flatMap(group => group.weights);
  const shards = manifest.weightsManifest.flatMap(group =>
    group.paths.map(name => fs.readFileSync(path.join(folder, name))));
  const bytes = Buffer.concat(shards);
  const weightData = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  return tf.loadLayersModel(tf.io.fromMemory({
    modelTopology: manifest.modelTopology, weightSpecs: specs, weightData,
  }));
}

export function imageTensor(imagePath, size) {
  const bytes = fs.readFileSync(imagePath);
  const decoded = path.extname(imagePath).toLowerCase() === ".png"
    ? PNG.sync.read(bytes) : jpeg.decode(bytes, { useTArray: true });
  const rgb = new Uint8Array(decoded.width * decoded.height * 3);
  for (let src = 0, dst = 0; dst < rgb.length; src += 4, dst += 3) {
    rgb[dst] = decoded.data[src];
    rgb[dst + 1] = decoded.data[src + 1];
    rgb[dst + 2] = decoded.data[src + 2];
  }
  return tf.tidy(() => tf.image.resizeBilinear(
    tf.tensor3d(rgb, [decoded.height, decoded.width, 3]), [size, size], true,
  ).toFloat().div(127.5).sub(1).expandDims(0));
}
