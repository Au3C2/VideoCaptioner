# 开发原则（Development Principles）

本文件约束本仓库内所有人工与 AI 辅助开发的提交、合并与测试行为。

## 1. 分支模型

- `master` 是唯一的长期分支，必须**随时保持可发布状态**。CI 守卫在每次 push 时运行
  （`.github/workflows/ci.yml` 运行 `pytest tests/test_cli tests/test_dubbing`；
  `publish-pypi.yml` 运行 `pytest tests/test_cli/`）。
- **琐碎改动**（文档、单文件修复）：直接提交到 `master` 并 push。
- **大型或有风险的工作**（多文件、影响生产代码、改动公共接口）：从 `master` 切出
  短生命周期分支 `feature/<topic>`，通过 PR 合回 `master`，合并后**立即删除分支**。
- 分支生命周期目标 **< 2 周**。超期分支必须合并，或经 cherry-pick 验证无遗留价值后删除。
- **禁止平行分支**：不允许两个分支同时演进同一特性（历史上曾因此产生 13 个孤儿提交
  和 106 个重复提交）。

## 2. 强制合并前审查（Mandatory Pre-Merge Review）

- 合并**任何** PR 到 `master` 之前，作者 agent **必须**派出一个独立子 agent
  （review agent）对该分支 diff 做完整的代码、逻辑、安全与回归审查。
- 审查意见必须在合并前处理完毕；未通过审查的 PR 不得合并。

## 3. 测试规范（本项目约定）

- 环境统一使用 uv：`uv sync --group dev` 安装依赖；`uv run pytest` 运行测试。
- 依赖外部服务的测试（Google/Bing/DeepLX 翻译、真实 LLM API 等）必须标注
  `@pytest.mark.integration`；默认在 `pyproject.toml` 的 `addopts` 中排除，
  需要时用 `uv run pytest -m integration` 手动运行。
- 依赖 LLM 的用例一律使用 `tests/conftest.py` 中的 `mock_llm_client` fixture
  离线运行，不得要求真实 API key。
- 测试之间必须相互隔离：禁止在测试体内遗留全局状态（缓存开关、模块级单例、
  持久缓存条目等）。如需临时修改全局状态，必须用 fixture 恢复进入前的状态。

## 4. 提交规范

- Commit message 使用 Conventional Commits（`feat:`、`fix:`、`test:`、`docs:`、`chore:`）。
- 代码与注释使用英文；面向用户的文档可使用中文。
- 遵循仓库现有代码风格：路径处理用 `pathlib`，类型注解完整。
