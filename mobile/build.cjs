const fs = require('node:fs');
const path = require('node:path');
const { build } = require('esbuild');
const root = path.resolve(__dirname, '..');
const output = path.join(__dirname, 'www');
fs.mkdirSync(output, { recursive: true });
for (const name of ['cloud.css', 'cloud.js']) fs.copyFileSync(path.join(root, 'web', 'cloud', name), path.join(output, name));
const html = fs.readFileSync(path.join(root, 'web', 'cloud', 'index.html'), 'utf8')
  .replace('<script src="cloud.js"', '<script src="native.js" defer></script><script src="cloud.js"');
fs.writeFileSync(path.join(output, 'index.html'), html);
build({entryPoints: [path.join(__dirname, 'native.js')], bundle: true, outfile: path.join(output, 'native.js'),
  format: 'iife', target: 'es2020', minify: true}).catch(error => {console.error(error); process.exitCode = 1;});
