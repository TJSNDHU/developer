import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import LoopStepBar from '../frontend/src/components/LoopStepBar.jsx';

function assert(cond, msg) { if (!cond) throw new Error(msg); }
const html = renderToStaticMarkup(React.createElement(LoopStepBar, {phase:'error', retryCount:0, errorStep:2}));
console.log(html);
assert(html.includes('data-testid="loop-step-bar"'), 'step bar missing');
assert(html.includes('data-phase="error"'), 'bar phase error missing');
assert(html.includes('data-testid="loop-step-execute"'), 'execute step missing');
assert(/data-testid="loop-step-execute"[^>]*data-step-state="error"/.test(html), 'execute not red/error in SSR markup');
assert(!/data-testid="loop-step-execute"[^>]*data-step-state="active"/.test(html), 'execute still active');
console.log('PASS component LoopStepBar phase=error errorStep=2 renders execute error');
