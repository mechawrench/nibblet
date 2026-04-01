#!/usr/bin/env node
/**
 * Procedural blob character generator. Makes all seven states from a color
 * and a name. SVG frames → sips PNG → sips GIF → gifsicle animated.
 *
 *   node tools/make_blob.mjs "#4A90D9" bluey
 *   node tools/make_blob.mjs "#7B4A9E" grape
 *
 * Output: characters/<name>/ with manifest.json and seven .gif files.
 */

import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const W = 135, H = 102;
const CX = 67, CY = 58;

const [color = "#4A90D9", name = "blob"] = process.argv.slice(2);
const outDir = join(process.cwd(), "characters", name);
const tmp = mkdtempSync(join(tmpdir(), "blob-"));

// --- SVG frame builders ------------------------------------------------

const svg = (body) => `<svg width="${W}" height="${H}" xmlns="http://www.w3.org/2000/svg">
<rect width="${W}" height="${H}" fill="#000"/>
${body}
</svg>`;

function blob(t, opts = {}) {
  const { rx = 38, ry = 28, bob = 4, wobble = 2, squish = 0 } = opts;
  const y = CY + Math.sin(t * Math.PI * 2) * bob;
  const w = rx + Math.sin(t * Math.PI * 4) * wobble;
  const h = ry - squish + Math.cos(t * Math.PI * 4) * wobble * 0.6;
  return `<ellipse cx="${CX}" cy="${y}" rx="${w.toFixed(1)}" ry="${h.toFixed(1)}" fill="${color}"/>`;
}

function eyes(t, opts = {}) {
  const { blink = false, spiral = false, bob = 4, spread = 13, r = 5 } = opts;
  const y = CY - 6 + Math.sin(t * Math.PI * 2) * bob;
  const lx = CX - spread, rx = CX + spread;
  if (spiral) {
    const a = t * 720;
    return [lx, rx].map(x =>
      `<g transform="translate(${x},${y}) rotate(${a})"><path d="M0,0 Q4,-2 3,2 Q-2,4 -2,-2" stroke="#000" stroke-width="1.2" fill="none"/></g>`
    ).join("");
  }
  if (blink) {
    return `<line x1="${lx-5}" y1="${y}" x2="${lx+5}" y2="${y}" stroke="#000" stroke-width="2"/>
            <line x1="${rx-5}" y1="${y}" x2="${rx+5}" y2="${y}" stroke="#000" stroke-width="2"/>`;
  }
  return `<circle cx="${lx}" cy="${y}" r="${r}" fill="#000"/>
          <circle cx="${rx}" cy="${y}" r="${r}" fill="#000"/>
          <circle cx="${lx+1.5}" cy="${y-1.5}" r="1.8" fill="#fff"/>
          <circle cx="${rx+1.5}" cy="${y-1.5}" r="1.8" fill="#fff"/>`;
}

const sweat = (t) => {
  const y = 20 + (t * 60) % 60;
  return `<ellipse cx="${CX+32}" cy="${y}" rx="3" ry="5" fill="#5BA3E0" opacity="0.8"/>`;
};

const bang = (t) => {
  const s = 1 + Math.sin(t * Math.PI * 6) * 0.15;
  return `<text x="${CX}" y="22" font-size="${(22*s).toFixed(1)}" font-weight="bold"
          fill="#E84545" text-anchor="middle" font-family="sans-serif">!</text>`;
};

const sparkles = (t) => {
  const out = [];
  for (let i = 0; i < 5; i++) {
    const a = (t + i * 0.2) * Math.PI * 2;
    const x = CX + Math.cos(a + i) * 48;
    const y = 30 + ((t * 80 + i * 20) % 70);
    out.push(`<circle cx="${x.toFixed(0)}" cy="${y.toFixed(0)}" r="2" fill="#FFD54A"/>`);
  }
  return out.join("");
};

const heart = (t) => {
  const y = 28 - t * 16;
  const s = 1 + t * 0.4;
  const o = Math.max(0, 1 - t);
  return `<g transform="translate(${CX},${y.toFixed(1)}) scale(${s.toFixed(2)})" opacity="${o.toFixed(2)}">
    <path d="M0,4 C-6,-2 -12,2 -6,8 L0,14 L6,8 C12,2 6,-2 0,4 Z" fill="#E84545"/></g>`;
};

// --- State definitions -------------------------------------------------

const STATES = {
  sleep: { frames: 24, delay: 8, build: (t) =>
    blob(t, { bob: 2, wobble: 0.5 }) + eyes(t, { blink: true, bob: 2 }) +
    `<text x="${CX+36}" y="${(32 + Math.sin(t*Math.PI*2)*3).toFixed(0)}" font-size="10" fill="#666" font-family="sans-serif">z</text>`
  },
  idle: { frames: 30, delay: 6, build: (t) => {
    const blink = t > 0.85 && t < 0.92;
    return blob(t) + eyes(t, { blink });
  }},
  busy: { frames: 20, delay: 4, build: (t) =>
    blob(t, { bob: 6, wobble: 3 }) + eyes(t, { bob: 6 }) + sweat(t)
  },
  attention: { frames: 20, delay: 5, build: (t) =>
    blob(t, { bob: 3, squish: 2 }) + eyes(t, { bob: 3, r: 6 }) + bang(t)
  },
  celebrate: { frames: 24, delay: 4, build: (t) =>
    blob(t, { bob: 8, wobble: 4 }) + eyes(t, { bob: 8 }) + sparkles(t)
  },
  dizzy: { frames: 24, delay: 5, build: (t) =>
    blob(t, { bob: 2, wobble: 5 }) + eyes(t, { spiral: true, bob: 2 })
  },
  heart: { frames: 20, delay: 5, build: (t) =>
    blob(t, { bob: 3 }) + eyes(t, { bob: 3 }) + heart(t)
  },
};

// --- Render pipeline ---------------------------------------------------

function renderState(state, cfg) {
  const gifFrames = [];
  for (let i = 0; i < cfg.frames; i++) {
    const t = i / cfg.frames;
    const svgPath = join(tmp, `${state}_${String(i).padStart(3,"0")}.svg`);
    const pngPath = svgPath.replace(".svg", ".png");
    const gifPath = svgPath.replace(".svg", ".gif");
    writeFileSync(svgPath, svg(cfg.build(t)));
    execFileSync("sips", ["-s", "format", "png", svgPath, "--out", pngPath], { stdio: "pipe" });
    execFileSync("sips", ["-s", "format", "gif", pngPath, "--out", gifPath], { stdio: "pipe" });
    gifFrames.push(gifPath);
  }
  const out = join(outDir, `${state}.gif`);
  execFileSync("gifsicle", [
    "--colors", "64", "--delay", String(cfg.delay), "-O1", "--loopcount=0",
    ...gifFrames, "-o", out,
  ]);
  return out;
}

// --- Main --------------------------------------------------------------

mkdirSync(outDir, { recursive: true });
console.log(`generating ${name} (${color}) → ${outDir}`);

const states = {};
for (const [state, cfg] of Object.entries(STATES)) {
  process.stdout.write(`  ${state.padEnd(10)} `);
  renderState(state, cfg);
  const size = execFileSync("stat", ["-f%z", join(outDir, `${state}.gif`)]).toString().trim();
  console.log(`${cfg.frames}f → ${(size/1024).toFixed(1)}KB`);
  states[state] = `${state}.gif`;
}

writeFileSync(join(outDir, "manifest.json"), JSON.stringify({
  name,
  colors: { body: color, bg: "#000000", text: "#FFFFFF", textDim: "#808080", ink: "#000000" },
  states,
}, null, 2));

rmSync(tmp, { recursive: true });
const total = execFileSync("du", ["-sk", outDir]).toString().split("\t")[0];
console.log(`\n${total}KB total — ready to install`);
