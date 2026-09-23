import {test} from 'node:test';
import assert from 'node:assert/strict';
import {briefPresentation} from '../src/briefPresentation.ts';

test('candidate-only citations become links without changing existing URLs',()=>{
  const input='观点 [证据：record-a]\n\n- [新闻](https://example.org/record-a) · 2026-09-20 · record-a';
  const output=briefPresentation(input);
  assert.ok(output.includes('观点 [证据：[来源 1](https://example.org/record-a)]'));
  assert.ok(output.includes('[新闻](https://example.org/record-a)'));
});

test('unresolved IDs stay visible and matrix/candidate duplicates share a number',()=>{
  const input='record-a unresolved\n- record-a · [新闻](https://example.org/a)\n- [新闻](https://example.org/a) · 未知 · record-a';
  const output=briefPresentation(input);
  assert.ok(output.includes('unresolved'));
  assert.ok(!output.includes('来源 2'));
  assert.equal(briefPresentation('没有引用索引 record-x'),'没有引用索引 record-x');
});
