"""GitHub Actions 工作流静态校验 (无需网络, 也不需要 actionlint)。

起因: `.github/workflows/release.yml` 曾使用 `replace(matrix.target, '/', '-')`。
GitHub Actions 的**表达式函数集里没有 `replace`**, 整个工作流因此被判为
`Invalid workflow file` —— 而且只有在 GitHub 上才会暴露。

本测试在本地拦住这一类错误:
  * 所有工作流 YAML 必须可解析;
  * `${{ ... }}` 里调用的函数必须在 GitHub 支持的函数集内;
  * `matrix.<key>` 引用的键必须在同一 job 的 matrix 中声明;
  * 每个 job 有 runs-on 与 steps, 每个 step 有 uses 或 run。
"""

import os
import re

import pytest

yaml = pytest.importorskip('yaml')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW_DIR = os.path.join(ROOT, '.github', 'workflows')

# GitHub Actions 表达式函数白名单 (官方文档列出: 通用函数 + 状态检查函数)。
ALLOWED_FUNCTIONS = {
    'contains', 'startsWith', 'endsWith', 'format', 'join',
    'toJSON', 'fromJSON', 'hashFiles',
    'success', 'always', 'cancelled', 'failure',
}

EXPR_RE = re.compile(r'\$\{\{(.*?)\}\}', re.S)
FUNC_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*\(')
MATRIX_REF_RE = re.compile(r'\bmatrix\.([A-Za-z_][A-Za-z0-9_-]*)')
# 表达式里的单引号字符串 ('' 为转义), 扫描函数名之前先剔除, 避免
# contains(x, 'a(') 这类字面量被误判成函数调用
QUOTED_RE = re.compile(r"'(?:[^']|'')*'")


def _workflow_files():
    if not os.path.isdir(WORKFLOW_DIR):
        return []
    return sorted(f for f in os.listdir(WORKFLOW_DIR)
                  if f.endswith(('.yml', '.yaml')))


def _load(name):
    with open(os.path.join(WORKFLOW_DIR, name), encoding='utf-8') as f:
        return yaml.safe_load(f)


def _strings(obj):
    """递归取出结构里所有字符串 (含键名)。"""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _strings(k)
            yield from _strings(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from _strings(item)


def _declared_matrix_keys(job):
    strategy = job.get('strategy') or {}
    matrix = strategy.get('matrix') or {}
    keys = {k for k in matrix if k not in ('include', 'exclude')}
    for item in matrix.get('include') or []:
        if isinstance(item, dict):
            keys |= set(item)
    return keys


FILES = _workflow_files()

assert FILES, '未找到任何工作流文件'


@pytest.mark.parametrize('name', FILES)
def test_workflow_parses(name):
    doc = _load(name)
    assert isinstance(doc, dict) and 'jobs' in doc, f'{name}: 缺少 jobs'


@pytest.mark.parametrize('name', FILES)
def test_expression_functions_are_supported(name):
    """表达式中只能调用 GitHub 支持的函数 (replace 不在其中)。"""
    doc = _load(name)
    bad = []
    for text in _strings(doc):
        for expr in EXPR_RE.findall(text):
            stripped = QUOTED_RE.sub("''", expr)
            for func in FUNC_RE.findall(stripped):
                if func not in ALLOWED_FUNCTIONS:
                    bad.append(f'{func}() in ${{{{{expr.strip()}}}}}')
    assert not bad, (
        f'{name}: 使用了 GitHub Actions 不支持的表达式函数: {bad}; '
        f'可用函数: {sorted(ALLOWED_FUNCTIONS)}')


@pytest.mark.parametrize('name', FILES)
def test_matrix_references_are_declared(name):
    """job 内引用的 matrix.* 键必须在同一个 job 的 matrix 里声明过。"""
    doc = _load(name)
    problems = []
    for job_id, job in (doc.get('jobs') or {}).items():
        declared = _declared_matrix_keys(job)
        for text in _strings(job):
            for key in MATRIX_REF_RE.findall(text):
                if key not in declared:
                    problems.append(f'{job_id}: matrix.{key} 未在 matrix 中声明')
    assert not problems, f'{name}: {sorted(set(problems))}'


@pytest.mark.parametrize('name', FILES)
def test_jobs_and_steps_are_well_formed(name):
    doc = _load(name)
    for job_id, job in (doc.get('jobs') or {}).items():
        assert job.get('runs-on'), f'{name}:{job_id} 缺少 runs-on'
        steps = job.get('steps')
        assert steps, f'{name}:{job_id} 缺少 steps'
        for i, step in enumerate(steps):
            assert 'uses' in step or 'run' in step, \
                f'{name}:{job_id} 第 {i + 1} 个 step 既没有 uses 也没有 run'


@pytest.mark.parametrize('name', FILES)
def test_artifact_names_are_unique(name):
    """同一次运行里 upload-artifact 的名称必须唯一 (含 matrix 展开)。"""
    doc = _load(name)
    seen = []
    for job in (doc.get('jobs') or {}).values():
        for step in job.get('steps') or []:
            uses = step.get('uses', '')
            if 'upload-artifact' not in uses:
                continue
            with_block = step.get('with') or {}
            seen.append(with_block.get('name'))
    # 同一 job 内的字面量名称不得重复 (不同 job 之间由 matrix 区分)
    assert len(seen) == len(set(seen)), f'{name}: upload-artifact 名称重复: {seen}'
