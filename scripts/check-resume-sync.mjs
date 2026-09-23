/**
 * 简历双源一致性校验 —— 构建前置硬闸门。
 *
 * 站点阅读形态（`src/data/profile.ts` 渲染的 `/resume`）与投递形态（`public/resume/resume.pdf`）
 * 是两份人工维护的内容。RESUME.md 里"改简历记得两处同步"这类约定在本仓库已被证明不可靠
 * （见 check-figures 的存在理由），所以这里把约定变成断言：
 *
 *   站点侧的关键事实，必须都能在 PDF 文本里找到 —— 缺一条即构建失败。
 *
 * 方向是**单向包含**（PDF ⊇ 站点侧事实）：站点刻意只展示 PDF 的子集，
 * 首页与展示区还用弹性区间，所以"PDF 里多出内容"不算问题。
 *
 * 用法：
 *   node scripts/check-resume-sync.mjs            # 闸门模式
 *   node scripts/check-resume-sync.mjs --dump     # 额外打印从 profile 抽出的事实清单
 */
import { readFile } from 'node:fs/promises';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createRequire } from 'node:module';
import path from 'node:path';

const require = createRequire(import.meta.url);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const pdfPath = path.join(root, 'public', 'resume', 'resume.pdf');
const dump = process.argv.includes('--dump');

// Node 24 默认剥离类型标记，可直接 import 站点的 .ts 数据源 —— 事实清单不另抄一份
const profile = await import(pathToFileURL(path.join(root, 'src', 'data', 'profile.ts')).href);

/** PDF 抽出的文本没有排版信息：比较前把空白全部去掉，只比字符序列 */
const squash = (text) => text.replace(/\s+/g, '');

/** 数字与带后缀的数字（19、30+、80%、170+…）：履历里的量化事实，必须逐条对上 */
const numbersIn = (text) => text.match(/\d+\s*(?:\+|%|pd|min\+?)?/g) ?? [];

/**
 * 从 profile 抽出"必须在 PDF 里出现"的事实。
 * 刻意不手写清单：手写清单会随内容改动而漂移，抽出来的清单永远跟着真源走。
 */
function collectFacts() {
  const facts = [];

  const { identity, contact, experience, education } = profile;
  facts.push({ kind: '姓名', value: identity.name });

  for (const job of experience) {
    facts.push({ kind: '雇主', value: job.company });
    // 时间区间拆成年月：PDF 里的连接符（– / -）与"至今"写法可能变，只断言年月本身
    for (const stamp of job.period.match(/\d{4}\.\d{2}/g) ?? []) {
      facts.push({ kind: '时间区间', value: stamp });
    }
    for (const point of job.highlights) {
      for (const num of numbersIn(point)) {
        facts.push({ kind: '指标数值', value: num.trim() });
      }
    }
  }

  for (const item of education) {
    facts.push({ kind: '学校', value: item.school });
    facts.push({ kind: '学位', value: item.degree.split('·').pop().trim() });
    for (const stamp of item.period.match(/\d{4}\.\d{2}/g) ?? []) {
      facts.push({ kind: '时间区间', value: stamp });
    }
    for (const note of item.notes) {
      for (const num of numbersIn(note)) {
        facts.push({ kind: '成果数值', value: num.trim() });
      }
    }
  }

  // 邮箱也要对得上：PDF 是投递材料，邮箱写错等于简历作废
  facts.push({ kind: '邮箱', value: contact.email });

  // 方向标签刻意不校验：PDF 里是"Agent 评测/可观测工程师"这类职位名，
  // 站点侧是能力标签，两者是不同粒度的表述，字符串包含关系不成立也不该成立

  // 去重（同一数值可能来自多处）
  const seen = new Set();
  return facts.filter((fact) => {
    const key = `${fact.kind}\u0000${fact.value}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

const facts = collectFacts();

if (dump) {
  console.log('\n----- 从 src/data/profile.ts 抽出的事实清单 -----');
  for (const fact of facts) {
    console.log(`  [${fact.kind}] ${fact.value}`);
  }
  console.log('');
}

const bytes = await readFile(pdfPath).catch(() => null);
if (!bytes) {
  console.error(
    '[resume-sync] FAIL 找不到简历 PDF —— 双源校验无从进行，构建中止\n' +
      `  期望路径：${path.relative(root, pdfPath)}\n` +
      '  简历 PDF 是投递形态的唯一原件，缺失时不允许静默跳过（站点文本页会照常发布，但两源不再可校验）。',
  );
  process.exit(1);
}

const pdfjs = await import(pathToFileURL(require.resolve('pdfjs-dist/legacy/build/pdf.mjs')).href);
const doc = await pdfjs.getDocument({ data: new Uint8Array(bytes) }).promise;

let pdfText = '';
for (let page = 1; page <= doc.numPages; page += 1) {
  const content = await (await doc.getPage(page)).getTextContent();
  pdfText += content.items.map((item) => item.str).join('');
}

const haystack = squash(pdfText);
const missing = facts.filter((fact) => !haystack.includes(squash(fact.value)));

console.log(
  `[resume-sync] 校验 ${path.relative(root, pdfPath)}（${doc.numPages} 页）· 站点侧事实 ${facts.length} 条 · 缺失 ${missing.length} 条`,
);

if (missing.length > 0) {
  console.error(
    `\n[resume-sync] FAIL 站点侧事实在简历 PDF 里找不到，构建中止：\n\n${missing
      .map((fact) => `    [${fact.kind}] ${fact.value}`)
      .join('\n')}\n`,
  );
  console.error('  两种情况：');
  console.error('    1) 简历 PDF 更新后站点侧没跟着改 —— 改 src/data/profile.ts 对齐');
  console.error('    2) 站点侧写错了（错字、编号过期、数字不准）—— 以 PDF 原件为准修正，或先更新 PDF');
  console.error('  查站点侧当前抽出了哪些事实：node scripts/check-resume-sync.mjs --dump');
  process.exit(1);
}

console.log('[resume-sync] PASS 站点侧事实在简历 PDF 中均可找到');
