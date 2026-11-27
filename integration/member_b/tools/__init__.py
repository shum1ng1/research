# Vendored from the member B archive (Analysis/Writer).
# Imports rewritten to integration.member_b so this tree does not collide with legacy root tools/ or member A.
# -*- coding: utf-8 -*-
"""B 的工具层：无状态纯函数，被 analysis_agent / writer_agent 调用。

这里不联网、不调 LLM、不碰 A 的 ID；输入 dict / list，输出 dict / list。
"""
