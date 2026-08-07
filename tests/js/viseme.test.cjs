global.window = {};
require("../../static/live2d/viseme.js");

const V = global.window.PLViseme;
const assert = (cond, msg) => {
  if (!cond) {
    console.error("FAIL: " + msg);
    process.exitCode = 1;
  } else {
    console.log("ok: " + msg);
  }
};

assert(V.detectLanguage("你好世界") === "zh", "detect zh");
assert(V.detectLanguage("こんにちは") === "ja", "detect ja");
assert(V.detectLanguage("hello world") === "en", "detect en");

const zh = V.estimateVisemeUnits("你好", "zh");
assert(zh.length === 2 && zh.every((u) => u === "aa"), "zh chars -> aa");

const en = V.estimateVisemeUnits("hello", "en");
assert(
  JSON.stringify(en) === JSON.stringify(["aa", "aa", "neutral", "neutral", "ow"]),
  "latin word visemes: " + JSON.stringify(en)
);

const ja = V.estimateVisemeUnits("あい", "ja");
assert(JSON.stringify(ja) === JSON.stringify(["aa", "iy"]), "kana visemes: " + JSON.stringify(ja));

const punct = V.estimateVisemeUnits("你好。", "zh");
assert(punct[punct.length - 1] === "mbp", "punct -> mbp");

const poses = V.allocatePoses(["aa", "iy"], 2, "zh");
assert(poses.length === 14, "allocate count = 14, got " + poses.length);
assert(Math.abs(V.probePose(poses, 0.05).end - 2 / 14) < 1e-9, "probe first");
assert(V.probePose(poses, 1.99).form === 0, "trailing slots pad with neutral form");

const m1 = V.computeMouth(0.2, 0.85, 0.0);
assert(Math.abs(m1.open - 0.85) < 1e-9, "aa full activity open=0.85");
const m2 = V.computeMouth(0.2, 0.0, 0.0);
assert(m2.open === 0.04, "mbp -> small open");
const m3 = V.computeMouth(0.1, 0.38, -1.0);
assert(m3.form < 0 && m3.open > 0, "uw blend form negative");

console.log("done");
