const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const source = fs.readFileSync(path.join(__dirname, "../src/SessionGate.tsx"), "utf8");
const css = fs.readFileSync(path.join(__dirname, "../src/login.css"), "utf8");

test("checking the session shows a neutral splash, not the login page", () => {
  const splash = source.indexOf('if(checking&&!session)return <main className="session-splash"');
  assert.ok(splash > 0, "checking state should render the splash");
  assert.ok(splash < source.indexOf('className="login-page"'), "splash must return before the login page renders");
  assert.match(css, /\.session-splash svg\{opacity:0;animation:[^}]*\.3s/);
});
