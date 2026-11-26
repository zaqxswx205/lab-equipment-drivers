"""Inline INI instructions for the drive profile editor."""

PROFILE_HELP = """; 驱动方案填写说明
; strategy=mixed：每阶段独立选择触发方式。
; heater_w填写加热功率：本行到温后测试，完成后放行下一阶段。
; trigger=time：at为从本次驱动启动起累计的秒数。
; trigger=temperature：at为降温阈值，单位K。
; trigger=time_or_temperature：at为累计秒数，temperature_at为降温阈值K，任一先到触发。
; 纯时间阈值递增、纯温度阈值递减；按文件阶段顺序执行，触发后不回退。
; time_unit=s：时间使用秒；temperature_unit=K：温度使用K。
; mode=voltage时value单位为V；mode=power时value单位为W。
; value=0表示关闭输出；最后阶段持续保持。
"""


PROFILE_EXAMPLES = """; 更多阶段示例（以下均为注释，不执行；按需取消注释并调整阈值）
; 时间均从本次驱动启动起累计，非上一阶段的保持时长。

; 示例3：第20秒切换到目标功率0.2W（time + power）。
; [Step 3]
; trigger = time
; at = 20
; mode = power
; value = 0.2

; 示例4：降温到283.15K时切换到3V（temperature + voltage）。
; [Step 4]
; trigger = temperature
; at = 283.15
; mode = voltage
; value = 3

; 示例5：第30秒或降温到273.15K，先到则切换到0.1W（time_or_temperature + power）。
; [Step 5]
; trigger = time_or_temperature
; at = 30
; temperature_at = 273.15
; mode = power
; value = 0.1

; 示例6：第40秒关闭输出（time + voltage，value=0）。
; [Step 6]
; trigger = time
; at = 40
; mode = voltage
; value = 0
"""

PERFORMANCE_HELP = """; 性能点联动填写说明
; 一行一个阶段：加热功率留空为降温阶段，填写heater_w则执行本行性能测试。
; mode/value是制冷机设定；heater_w是加热功率；温度列是本行目标温度(K)。
; 到达进入条件并到温后加载加热；完成后确认加热OFF，再进入下一行。
; stability_mode=manual手动下一阶段；duration持续指定时长；peak_to_peak窗口峰峰值判稳。
; 表格“判定时长(s)”：持续时长模式为测试秒数；峰峰值判稳模式为完整窗口秒数。
; 手动模式禁用时间和峰峰值；持续时长模式禁用峰峰值。原文仍分别保存duration_s/window_s以兼容旧文件。
; 测试期间暂停驱动计时；两台电源达标后才开始判稳计时，失去达标重置。
; 各行直接填写，不用另配Point文件或编号；保存后点击主界面“开始驱动”。
"""


LEGACY_PERFORMANCE_EXAMPLE = '; 完整性能点联动示例（以下均为注释，不参与执行）\n; 用法：在阶段表格按下列4行填写，或用以下Drive/Step内容替换当前有效方案。\n; 输出仅为格式示例，请按负载额定值和实验保护设置调整；测温保护范围须包含90K、100K。\n; Step 1：立即以3V降温；Step 2：到100K后保持20W，依次测100K@4W与100K@6W。\n; Step 3：前两个点完成、且降到90K后保持30W，测90K@3W；Step 4：全部完成后关闭输出。\n; [Drive]\n; strategy = mixed\n; time_unit = s\n; temperature_unit = K\n;\n; [Step 1]\n; trigger = time\n; at = 0\n; mode = voltage\n; value = 3\n;\n; [Step 2]\n; trigger = temperature\n; at = 100\n; mode = power\n; value = 20\n; performance_points = 1, 2\n;\n; [Step 3]\n; trigger = temperature\n; at = 90\n; mode = power\n; value = 30\n; performance_points = 3\n;\n; [Step 4]\n; trigger = time\n; at = 1\n; mode = voltage\n; value = 0\n;\n; 以下Point内容填写到“性能点测试…”方案中，不要作为本驱动文件的有效配置。\n; [Point 1]\n; temperature_k = 100\n; heater_w = 4\n; stability_mode = duration\n; duration_s = 600\n;\n; [Point 2]\n; temperature_k = 100\n; heater_w = 6\n; stability_mode = peak_to_peak\n; window_s = 300\n; peak_to_peak_k = 0.1\n;\n; [Point 3]\n; temperature_k = 90\n; heater_w = 3\n; stability_mode = peak_to_peak\n; window_s = 300\n; peak_to_peak_k = 0.1\n; 从“性能点测试…”开始：到温后自动加载，定时/判稳通过后放行后续阶段。\n'

PERFORMANCE_EXAMPLE = '; 统一阶段表格示例（以下为注释，不执行；请按实际负载调整输出和保护）\n; 依次：3V降温 → 100K@4W持续600秒 → 100K@6W峰峰值判稳 → 90K@3W判稳 → 关闭。\n; 温度重复的相邻测试行会等待上一行完成后，再按本行条件进入。\n; [Drive]\n; strategy = mixed\n; time_unit = s\n; temperature_unit = K\n;\n; [Step 1]\n; trigger = time\n; at = 0\n; mode = voltage\n; value = 3\n;\n; [Step 2]\n; trigger = temperature\n; at = 100\n; mode = power\n; value = 20\n; heater_w = 4\n; test_temperature_k = 100\n; stability_mode = duration\n; duration_s = 600\n;\n; [Step 3]\n; trigger = temperature\n; at = 100\n; mode = power\n; value = 20\n; heater_w = 6\n; test_temperature_k = 100\n; stability_mode = peak_to_peak\n; window_s = 300\n; peak_to_peak_k = 0.1\n;\n; [Step 4]\n; trigger = temperature\n; at = 90\n; mode = power\n; value = 30\n; heater_w = 3\n; test_temperature_k = 90\n; stability_mode = peak_to_peak\n; window_s = 300\n; peak_to_peak_k = 0.1\n;\n; [Step 5]\n; trigger = time\n; at = 1\n; mode = voltage\n; value = 0\n'




def with_profile_help(text):
    text = text.replace(LEGACY_PERFORMANCE_EXAMPLE, "")
    if "; 驱动方案填写说明" not in text:
        text = PROFILE_HELP + "\n" + text
    if "; 性能点联动填写说明" not in text:
        text = PERFORMANCE_HELP + "\n" + text
    # Refresh the generated performance instructions in previously saved schemes.
    import re
    text = re.sub(r"(?m)^; 性能点联动填写说明\n(?:;[^\n]*\n)*", lambda _: PERFORMANCE_HELP, text)
    if "; 统一阶段表格示例" not in text:
        text = text.rstrip() + "\n\n" + PERFORMANCE_EXAMPLE
    if "; 更多阶段示例" not in text:
        text = text.rstrip() + "\n\n" + PROFILE_EXAMPLES
    return text
