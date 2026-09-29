#!/usr/bin/env node
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import * as tf from "@tensorflow/tfjs-node";

const CANONICAL_CLASSES = ["valid", "invalid", "manual_review"];
const IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg"]);

function parseArgs(argv) {
  const args = {
    modelDir: path.resolve("gtm_model"),
    dataset: path.resolve("dataset_generator", "gtm_dataset", "validation"),
    output: null,
  };
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = argv[i + 1];
    if (arg === "--model-dir" && next) {
      args.modelDir = path.resolve(next);
      i += 1;
    } else if (arg === "--dataset" && next) {
      args.dataset = path.resolve(next);
      i += 1;
    } else if (arg === "--output" && next) {
      args.output = path.resolve(next);
      i += 1;
    } else if (arg === "--help" || arg === "-h") {
      console.log(`Usage:
  node gtm_model/evaluate_gtm.mjs [options]

Options:
  --model-dir <dir>  GTM export directory containing model.json, metadata.json, weights.bin
                     Default: ./gtm_model
  --dataset <dir>    Dataset split directory containing valid/, invalid/, manual_review/
                     Default: ./dataset_generator/gtm_dataset/validation
  --output <file>    JSON report path
                     Default: <dataset>/gtm_evaluation.json

Examples:
  node gtm_model/evaluate_gtm.mjs
  node gtm_model/evaluate_gtm.mjs --dataset dataset_generator/gtm_dataset/test
  node gtm_model/evaluate_gtm.mjs --model-dir gtm_model --dataset dataset_generator/gtm_dataset/validation
`);
      process.exit(0);
    } else {
      throw new Error(`Unknown or incomplete argument: ${arg}`);
    }
  }
  if (!args.output) {
    args.output = path.join(args.dataset, "gtm_evaluation.json");
  }
  return args;
}

function canonicalLabel(raw) {
  const text = String(raw ?? "")
    .trim()
    .toLowerCase()
    .replace(/[-_]+/g, " ")
    .replace(/\s+/g, " ");

  if (text.includes("manual") || text.includes("review")) return "manual_review";
  if (text.includes("invalid") || text.includes("reject")) return "invalid";
  if (text.includes("valid") || text.includes("approve")) return "valid";
  throw new Error(`Unsupported GTM label: ${JSON.stringify(raw)}`);
}

function requireFile(filePath) {
  if (!fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    throw new Error(`Required file not found: ${filePath}`);
  }
}

function listImages(folder) {
  if (!fs.existsSync(folder) || !fs.statSync(folder).isDirectory()) {
    throw new Error(`Expected dataset class folder not found: ${folder}`);
  }
  return fs
    .readdirSync(folder, { withFileTypes: true })
    .filter((entry) => entry.isFile() && IMAGE_EXTENSIONS.has(path.extname(entry.name).toLowerCase()))
    .map((entry) => path.join(folder, entry.name))
    .sort();
}

function safeDivide(numerator, denominator) {
  return denominator === 0 ? 0 : numerator / denominator;
}

function round(value, digits = 6) {
  return Number(Number(value).toFixed(digits));
}

function csvEscape(value) {
  const text = String(value ?? "");
  return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function confusionTemplate() {
  return Object.fromEntries(
    CANONICAL_CLASSES.map((actual) => [
      actual,
      Object.fromEntries(CANONICAL_CLASSES.map((predicted) => [predicted, 0])),
    ]),
  );
}

async function loadImageTensor(imagePath, imageSize) {
  const bytes = fs.readFileSync(imagePath);
  return tf.tidy(() => {
    const decoded = tf.node.decodeImage(bytes, 3);
    const resized = tf.image.resizeBilinear(decoded, [imageSize, imageSize], true);
    return resized.toFloat().div(127.5).sub(1).expandDims(0);
  });
}

async function main() {
  const args = parseArgs(process.argv);
  const modelPath = path.join(args.modelDir, "model.json");
  const metadataPath = path.join(args.modelDir, "metadata.json");
  requireFile(modelPath);
  requireFile(metadataPath);

  const metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8"));
  if (!Array.isArray(metadata.labels) || metadata.labels.length === 0) {
    throw new Error("metadata.json does not contain a non-empty labels array");
  }

  const modelLabels = metadata.labels.map(canonicalLabel);
  if (new Set(modelLabels).size !== modelLabels.length) {
    throw new Error(
      `GTM metadata labels collapse to duplicate AssureX classes: ${JSON.stringify(metadata.labels)}`,
    );
  }

  for (const required of CANONICAL_CLASSES) {
    if (!modelLabels.includes(required)) {
      throw new Error(
        `GTM model is missing required class ${required}. Metadata labels: ${JSON.stringify(metadata.labels)}`,
      );
    }
  }

  const imageSize = Number(metadata.imageSize) || 224;
  const model = await tf.loadLayersModel(pathToFileURL(modelPath).href);

  const confusion = confusionTemplate();
  const predictions = [];
  const confidenceTotals = Object.fromEntries(CANONICAL_CLASSES.map((c) => [c, 0]));
  const trueClassConfidenceTotals = Object.fromEntries(CANONICAL_CLASSES.map((c) => [c, 0]));
  const support = Object.fromEntries(CANONICAL_CLASSES.map((c) => [c, 0]));
  let correct = 0;
  let total = 0;
  let topConfidenceSum = 0;

  for (const expected of CANONICAL_CLASSES) {
    const classFolder = path.join(args.dataset, expected);
    const images = listImages(classFolder);
    if (images.length === 0) {
      throw new Error(`No images found in ${classFolder}`);
    }

    for (const imagePath of images) {
      const input = await loadImageTensor(imagePath, imageSize);
      let output;
      try {
        const raw = model.predict(input);
        output = Array.isArray(raw) ? raw[0] : raw;
        const values = Array.from(await output.data());

        if (values.length !== modelLabels.length) {
          throw new Error(
            `Model produced ${values.length} outputs for ${modelLabels.length} labels`,
          );
        }

        let bestIndex = 0;
        for (let index = 1; index < values.length; index += 1) {
          if (values[index] > values[bestIndex]) bestIndex = index;
        }

        const predicted = modelLabels[bestIndex];
        const topConfidence = values[bestIndex];
        const expectedIndex = modelLabels.indexOf(expected);
        const trueClassConfidence = values[expectedIndex];

        const scores = {};
        for (let index = 0; index < modelLabels.length; index += 1) {
          scores[modelLabels[index]] = round(values[index]);
        }

        total += 1;
        support[expected] += 1;
        confusion[expected][predicted] += 1;
        topConfidenceSum += topConfidence;
        confidenceTotals[predicted] += topConfidence;
        trueClassConfidenceTotals[expected] += trueClassConfidence;
        if (predicted === expected) correct += 1;

        predictions.push({
          file: path.relative(process.cwd(), imagePath),
          expected,
          predicted,
          correct: predicted === expected,
          confidence: round(topConfidence),
          true_class_confidence: round(trueClassConfidence),
          scores,
        });
      } finally {
        input.dispose();
        if (output && typeof output.dispose === "function") output.dispose();
      }
    }
  }

  const perClass = {};
  for (const label of CANONICAL_CLASSES) {
    const tp = confusion[label][label];
    const fp = CANONICAL_CLASSES.reduce(
      (sum, actual) => sum + (actual === label ? 0 : confusion[actual][label]),
      0,
    );
    const fn = CANONICAL_CLASSES.reduce(
      (sum, predicted) => sum + (predicted === label ? 0 : confusion[label][predicted]),
      0,
    );
    const precision = safeDivide(tp, tp + fp);
    const recall = safeDivide(tp, tp + fn);
    const f1 = safeDivide(2 * precision * recall, precision + recall);

    perClass[label] = {
      support: support[label],
      correct: tp,
      precision: round(precision),
      recall: round(recall),
      f1: round(f1),
      average_true_class_confidence: round(
        safeDivide(trueClassConfidenceTotals[label], support[label]),
      ),
    };
  }

  const macroPrecision =
    CANONICAL_CLASSES.reduce((sum, c) => sum + perClass[c].precision, 0) /
    CANONICAL_CLASSES.length;
  const macroRecall =
    CANONICAL_CLASSES.reduce((sum, c) => sum + perClass[c].recall, 0) /
    CANONICAL_CLASSES.length;
  const macroF1 =
    CANONICAL_CLASSES.reduce((sum, c) => sum + perClass[c].f1, 0) /
    CANONICAL_CLASSES.length;

  const report = {
    model_dir: path.relative(process.cwd(), args.modelDir) || ".",
    dataset: path.relative(process.cwd(), args.dataset) || ".",
    image_size: imageSize,
    metadata_labels: metadata.labels,
    canonical_labels: modelLabels,
    total_images: total,
    correct,
    incorrect: total - correct,
    accuracy: round(safeDivide(correct, total)),
    average_top_confidence: round(safeDivide(topConfidenceSum, total)),
    macro_precision: round(macroPrecision),
    macro_recall: round(macroRecall),
    macro_f1: round(macroF1),
    per_class: perClass,
    confusion_matrix: confusion,
    misclassified: predictions.filter((row) => !row.correct),
  };

  fs.mkdirSync(path.dirname(args.output), { recursive: true });
  fs.writeFileSync(args.output, JSON.stringify(report, null, 2) + "\n", "utf8");

  const csvPath = args.output.replace(/\.json$/i, "") + "_predictions.csv";
  const csvLines = [
    [
      "file",
      "expected",
      "predicted",
      "correct",
      "confidence",
      "true_class_confidence",
      ...CANONICAL_CLASSES.map((c) => `score_${c}`),
    ].join(","),
    ...predictions.map((row) =>
      [
        row.file,
        row.expected,
        row.predicted,
        row.correct,
        row.confidence,
        row.true_class_confidence,
        ...CANONICAL_CLASSES.map((c) => row.scores[c]),
      ]
        .map(csvEscape)
        .join(","),
    ),
  ];
  fs.writeFileSync(csvPath, csvLines.join("\n") + "\n", "utf8");

  console.log("");
  console.log("AssureX GTM Evaluation");
  console.log("======================");
  console.log(`Dataset: ${args.dataset}`);
  console.log(`Images: ${total}`);
  console.log(`Accuracy: ${(report.accuracy * 100).toFixed(2)}%`);
  console.log(`Macro F1: ${report.macro_f1.toFixed(4)}`);
  console.log(`Average top confidence: ${(report.average_top_confidence * 100).toFixed(2)}%`);
  console.log("");
  console.log("Per class:");
  for (const label of CANONICAL_CLASSES) {
    const m = perClass[label];
    console.log(
      `  ${label.padEnd(13)} precision=${m.precision.toFixed(4)} recall=${m.recall.toFixed(4)} f1=${m.f1.toFixed(4)} support=${m.support}`,
    );
  }
  console.log("");
  console.log("Confusion matrix (actual rows -> predicted columns):");
  console.log(`                 ${CANONICAL_CLASSES.map((c) => c.padStart(13)).join("")}`);
  for (const actual of CANONICAL_CLASSES) {
    console.log(
      `  ${actual.padEnd(13)}${CANONICAL_CLASSES.map((pred) =>
        String(confusion[actual][pred]).padStart(13),
      ).join("")}`,
    );
  }
  console.log("");
  console.log(`JSON report: ${args.output}`);
  console.log(`Predictions CSV: ${csvPath}`);
}

main().catch((error) => {
  console.error(`GTM evaluation failed: ${error.message}`);
  process.exitCode = 1;
});
