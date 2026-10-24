---
id: P<id>
title: <一句话标题>
goal: <一句话目标>
depends_on: []
model: sonnet
lane: worker
allowed_files:
  - <相对仓库根的路径>
forbidden_files:
  - <相对仓库根的路径或 "*">
inputs:
  - <工人需要读的文件或章节>
deliverables:
  - <交付的文件和它们做什么>
tests_first:
  - test_<先写的测试名>
acceptance_cmds:
  - python -m unittest discover -s tools/poc/tests -p "test_<pkg>*.py"
  - python tools/check_docs.py
  - python tools/check_lf.py
effort_h: <小时>
day: <第几天>
stop_rule: <做不完时交什么>
---

# P<id> <标题>

## 目标

<为什么要这个包，它回答什么问题>

## 输入

<文件、章节、契约>

## 交付物

<文件和命令行>

## 允许改的文件

前言 `allowed_files`。

## 禁止改的文件

前言 `forbidden_files`。

## 先写的测试

前言 `tests_first`，先提交（提交信息 `P<id>: tests first`），在基线提交上必须失败。

## 验收命令

前言 `acceptance_cmds`，全部退出码 0。

## 完成的定义

<可以机械判断的句子>

## 验证者清单

1. 白名单：diff 的每个文件都在 `allowed_files` 里。
2. 先红后绿。
3. 变异：<改坏哪一行，哪个测试应变红>。
4. 破坏性输入：<两个最可能的失败输入>。
