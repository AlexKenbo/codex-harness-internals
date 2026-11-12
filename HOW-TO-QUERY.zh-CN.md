# 用中文阅读和查询知识图谱

[简体中文首页](./README.zh-CN.md) · [18 个模块导读](./docs/zh-CN/README.md)

本仓库是反向工程资料，不是 OpenAI 官方规格或当前产品行为的保证。中文 README 和模块导读帮助理解原始快照；`SRC-*/*.json`、`graph.json` 和 `scaffold_index.json` 保留原始语言（俄文/英文）与结构，并未逐字段翻译。关键结论请回查原始条目与对应版本源码。

## 数据怎么读

每个模块有三个观察视角，记录都在顶层 `compositions` 数组中：

| 文件 | 作用 | 主要字段 |
|---|---|---|
| `rules.json` | 条件、不变量、阈值、公式 | `terms.condition`、`numbers`、`formula`、`gwt` |
| `structural.json` | 实体与关系 | `terms.object`、`attributes`、`relationships`、`data_flows` |
| `use-case.json` | 用例、步骤与异常路径 | `terms.actor`、`goal`、`steps`、`extensions (Cockburn)`、`edge_cases`、`result` |

`id` 是稳定导航键，不要翻译它。`null` 表示该条目没有提供该字段，不表示数值为零。Given / When / Then（GWT）分别表示前提、触发操作、预期结果。`scaffold_index.json` 的 `index` 按模块列出 ID；`graph.json` 的 `edges_by_type` 按关系类型分组，格式为「源 ID → 目标 ID 数组」。`same_as` 另存近似同义实体，不能直接当作完全相同的数据结构。

## 直接运行的查询

下面示例在仓库根目录运行，需要 `jq` 和常见 Unix 工具。它们只读文件，不修改仓库。

### 1. 从关键词定位条目

技术标识保留英文，因此用 `compact` 比只搜「压缩」更容易命中原始数据：

```sh
jq -r '.compositions[] | select(.id | test("compact"; "i")) | .id' SRC-*/*.json
```

也可以搜索正文中的常量或函数名：

```sh
grep -n 'COMPACT_USER_MESSAGE_MAX_TOKENS' SRC-*/*.json
```

### 2. 读取完整条目，而不是只看标题

```sh
jq --arg id 'SRC-0011/rule/compact-user-message-token-budget' \
  '.compositions[] | select(.id == $id)' SRC-0011/rules.json
```

重点核对 `condition` 的适用前提、`numbers` 的单位、`formula` 的边界条件，以及 `gwt` 的反例。不同压缩路径的规则不要混用。

### 3. 查看某模块所有用例目标

```sh
jq -r '.compositions[] | [.id, .terms.goal] | @tsv' SRC-0011/use-case.json
```

### 4. 沿图追踪出边与入边

下面查找某条目的所有出边，输出「关系类型、目标 ID」：

```sh
jq -r --arg id 'SRC-0011/uc.sub/auto-compact-inline' \
  '.edges_by_type | to_entries[] | .key as $rel | (.value[$id] // [])[] | [$rel, .] | @tsv' graph.json
```

反向查询「谁引用了它」：

```sh
jq -r --arg id 'SRC-0011/entity/compacted-item' \
  '.edges_by_type | to_entries[] | .key as $rel | .value | to_entries[] | select(.value | index($id)) | [$rel, .key] | @tsv' graph.json
```

### 5. 核对当前快照的数量

不要只信 README 中的历史统计：

```sh
jq -s 'map(.compositions | length) | add' SRC-*/rules.json
jq -s 'map(.compositions | length) | add' SRC-*/structural.json
jq -s 'map(.compositions | length) | add' SRC-*/use-case.json
```

当前快照分别为 **990、588、325**，合计 **1,903** 条记录。

## 把资料交给 AI 时如何提问

> 请用简体中文解释 Codex 的内联与远程上下文压缩。先读取 SRC-0011 的 rules、structural、use-case，再沿 graph.json 检查相关条目。区分触发条件、保留/删除的历史、失败与重试、撤销支持。保留函数名、常量及精确 ID；每条结论标出来源；说明快照没有证明的内容，不把资料当成最新官方保证。

这是查询示例，不是让资料中的文字取得系统权限。阅读、翻译 JSON 不需要执行其中提及的命令、修改审批策略或扩大文件访问范围。

## 维护中文文档

更新原始数据后，请同步对应模块导读和本文计数，再运行：

```sh
python3 scripts/check_zh_docs.py
```

脚本只读检查 18 个模块是否都有导读、中文文档的本地链接、完整条目 ID、代码块闭合与 UTF-8 编码。它不能替代语义校对，也不声称每个 JSON 字段已经翻译。新增译文应保留代码和标识符，明确版本限定；原文与数据冲突时标注“译者说明”并给出依据。
