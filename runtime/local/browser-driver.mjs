// Deterministic adapter; assertions are reviewed business input, never inferred from implementation.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const [requestFile, planFile, output] = process.argv.slice(2);
const request = JSON.parse(fs.readFileSync(requestFile, 'utf8'));
const plan = JSON.parse(fs.readFileSync(planFile, 'utf8'));
const receipt = { ...request, executor: 'factory-playwright', checks: [] };
delete receipt.base_url;
delete receipt.executable_path;
delete receipt.plan_sha256;
const artifact = name => ({ path: name, sha256: crypto.createHash('sha256').update(fs.readFileSync(path.join(output, name))).digest('hex') });
const project = process.env.FACTORY_BROWSER_PROJECT;
let browser;
let broken = false;
try {
  const require = createRequire(path.join(project, 'package.json'));
  const { chromium } = require('playwright');
  browser = await chromium.launch({ headless: true, ...(request.executable_path ? { executablePath: request.executable_path } : {}) });
  for (const check of plan.checks) {
    const rows = [];
    const files = [];
    let failed = false;
    try {
      assert.ok(Array.isArray(check.viewports) && check.viewports.length, 'viewports required');
      assert.ok(Array.isArray(check.steps) && check.steps.length, 'assertion steps required');
      assert.ok(check.steps.some(step => step.action?.startsWith('assert')), 'actions without assertions cannot pass');
      for (const [index, viewport] of check.viewports.entries()) {
        assert.ok(Number.isInteger(viewport.width) && viewport.width >= 200 && Number.isInteger(viewport.height) && viewport.height >= 200, 'invalid viewport');
        const context = await browser.newContext({ viewport });
        try {
          const page = await context.newPage();
          page.setDefaultTimeout(8000);
          const errors = [], api = [], external = [], navigations = [];
          const origin = new URL(request.base_url).origin;
          const assertOrigin = () => {
            assert.equal(new URL(page.url()).origin, origin, 'interaction navigated outside configured origin');
            assert.deepEqual(navigations, [], 'browser left configured origin');
          };
          page.on('pageerror', error => errors.push(error.message));
          page.on('framenavigated', frame => {
            if (frame === page.mainFrame() && frame.url() !== 'about:blank' && new URL(frame.url()).origin !== origin) navigations.push(frame.url());
          });
          page.on('popup', popup => { navigations.push('unexpected-popup'); popup.close().catch(() => {}); });
          page.on('request', item => {
            if (['xhr', 'fetch'].includes(item.resourceType())) api.push(item.url());
            if (new URL(item.url()).origin !== new URL(request.base_url).origin) external.push(item.url());
          });
          const destination = new URL(check.path || '/', request.base_url);
          assert.equal(destination.origin, new URL(request.base_url).origin, 'browser plan cannot change origin');
          assert.ok(!destination.username && !destination.password, 'browser URL cannot contain credentials');
          const response = await page.goto(destination.href);
          assert.ok(response && response.ok(), 'page did not load successfully');
          assert.equal(new URL(page.url()).origin, destination.origin, 'navigation redirected outside configured origin');
          const observations = [];
          for (const step of check.steps) {
            const locator = step.role ? page.getByRole(step.role, { name: step.name, exact: true })
              : step.text !== undefined ? page.getByText(step.text, { exact: true }) : step.selector ? page.locator(step.selector) : null;
            switch (step.action) {
              case 'click': await locator.click(); break;
              case 'press': await (locator || page.keyboard).press(step.key); break;
              case 'reload': await page.reload(); break;
              case 'assertText': await locator.waitFor({ state: 'visible' }); if (step.expected !== undefined) assert.equal(await locator.textContent(), step.expected); break;
              case 'assertDisabled': assert.equal(await locator.isDisabled(), step.expected); break;
              case 'assertVisible': assert.equal(await locator.isVisible(), true); break;
              case 'assertStyle': assert.equal(await locator.evaluate((element, property) => getComputedStyle(element).getPropertyValue(property), step.property), step.expected); break;
              case 'assertNoOverflow': assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth && document.body.scrollWidth <= document.documentElement.clientWidth), true); break;
              case 'assertNoStorage': assert.equal(await page.evaluate(() => localStorage.length + sessionStorage.length), 0); break;
              case 'assertNoApi': assert.deepEqual(api, []); break;
              case 'assertNoExternal': assert.deepEqual(external, []); break;
              case 'assertNoErrors': assert.deepEqual(errors, []); break;
              default: throw new Error('Unsupported browser action: ' + step.action);
            }
            assertOrigin();
            observations.push({ action: step.action, status: 'passed' });
          }
          assert.deepEqual(errors, [], 'browser runtime errors');
          assertOrigin();
          const name = `${check.id}-${index}.png`;
          await page.screenshot({ path: path.join(output, name), fullPage: true });
          assertOrigin();
          files.push(name);
          rows.push({ viewport, observations, status: 'passed' });
        } finally { await context.close(); }
      }
    } catch (error) { failed = true; broken = true; rows.push({ status: 'failed', error: String(error) }); }
    const name = `${check.id}.json`;
    fs.writeFileSync(path.join(output, name), JSON.stringify({ browser: browser.version(), plan_sha256: request.plan_sha256, rows }, null, 2));
    receipt.checks.push({ id: check.id, environment: check.environment, status: failed ? 'failed' : 'passed', artifacts: [artifact(name), ...files.map(artifact)] });
  }
} catch (error) {
  broken = true;
  fs.writeFileSync(path.join(output, 'driver-error.txt'), String(error));
  for (const check of request.checks) if (!receipt.checks.some(row => row.id === check.id)) receipt.checks.push({ id: check.id, environment: check.environment, status: 'blocked', artifacts: [artifact('driver-error.txt')] });
} finally {
  if (browser) await browser.close();
  fs.writeFileSync(path.join(output, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
}
process.exitCode = broken ? 1 : 0;
