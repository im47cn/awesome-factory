/**
 * commitlint 配置 —— 对齐 steering/git-conventions.md
 *
 * 规则等级：2 = error（阻断提交）| 1 = warn（警告不阻断）| 0 = 关闭
 * 文档：https://commitlint.js.org/reference/rules.html
 */
module.exports = {
  extends: ['@commitlint/config-conventional'],
  rules: {
    // ── type 必须在枚举内（强制，对应规范的 type 表）──────────────
    'type-enum': [
      2,
      'always',
      ['feat', 'fix', 'docs', 'style', 'refactor', 'perf', 'test', 'chore', 'revert'],
    ],

    // ── scope 建议在枚举内（warn，业务域靠 warn 放行，不阻断）─────
    //    下游通用最小子集：仅业务域 + dependency，不随上游技能清单扩展；
    //    上游完整枚举见 awesome-factory 根 commitlint.config.js（doc-freshness
    //    R9 门禁守护上游三方一致，本件为下游子集：R9 校验其枚举 ⊆ 根枚举且声明须带「下游」标注）
    'scope-enum': [1, 'always', ['api', 'db', 'ui', 'ci', 'dependency']],

    // ── 行长度（2026-09-16 数据驱动校准：6 仓 1353 提交实测，拦截率 ≤5% 红线，
    //    按 P99+幸存者偏差余量取整；旧 50/72/100 对 CJK 过紧已废）──
    'subject-max-length': [2, 'always', 100],
    'header-max-length': [2, 'always', 150],
    // body 单行：config-conventional 默认 100，中文长句/URL 易触线；实测 max 204
    'body-max-line-length': [2, 'always', 300],

    // ── 关闭英文大小写规则（中文 subject 不适用）──────────────────
    'subject-case': [0],
    'type-case': [0],
    'scope-case': [0],

    // ── body / footer 前置空行（推荐，提升可读性）─────────────────
    'body-leading-blank': [1, 'always'],
    'footer-leading-blank': [1, 'always'],

    // ── breaking change：config-conventional 已内置校验
    //    feat!: / BREAKING CHANGE: 两种写法均被识别，并触发 major bump
  },
};
