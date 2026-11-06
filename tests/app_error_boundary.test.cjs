const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const read = (f) => fs.readFileSync(path.join(__dirname, '../src', f), 'utf8');
test('a crash anywhere in the page renders an error with reload instead of a blank screen', () => {
  const main = read('main.tsx');
  assert.match(main, /<AppErrorBoundary><SessionGate><App\/><\/SessionGate><\/AppErrorBoundary>/);
  const boundary = read('AppErrorBoundary.tsx');
  assert.match(boundary, /static getDerivedStateFromError/);
  assert.match(boundary, /window\.location\.reload\(\)/);
  assert.match(read('style.css'), /\.app-crash\{/);
});
