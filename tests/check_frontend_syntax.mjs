/*
 * The dashboard is a single ~3,200-line index.html with no build step, so a
 * stray syntax error ships straight to production and only shows up as a blank
 * page. This compiles every inline <script> block without executing it.
 *
 * Usage: node tests/check_frontend_syntax.mjs [html]
 */

import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const target = process.argv[2] || 'index.html';
const html = await readFile(target, 'utf8');

const blocks = [...html.matchAll(/<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)<\/script>/g)]
  .map((match) => match[1])
  .filter((code) => code.trim().length > 0);

if (blocks.length === 0) {
  console.error(`FAIL no inline script blocks found in ${target}`);
  process.exit(1);
}

let failures = 0;
blocks.forEach((code, index) => {
  try {
    new vm.Script(code, { filename: `${target}#inline-script-${index}` });
  } catch (error) {
    failures += 1;
    console.error(`FAIL inline script ${index}: ${error.message}`);
  }
});

if (failures > 0) process.exit(1);
console.log(`OK parsed ${blocks.length} inline script block(s) from ${target}`);
