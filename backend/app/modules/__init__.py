"""业务模块包：7 个模块。

auth / identity_resolver / everos_gateway / covenant_store /
episode_editor / profile_viewer / audit_log

分期启用：第一期 auth + identity_resolver + covenant_store + everos_gateway(只读)；
第二期加 episode_editor + everos_gateway(写+cascade)；第三期加 profile_viewer。
audit_log 贯穿各写操作。
"""
