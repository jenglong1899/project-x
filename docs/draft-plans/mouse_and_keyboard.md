codex resume 019e1101-3a50-77a2-9e69-6affa38e4c08

用鼠标和键盘操作文件

给ai的prompt
```
<mouse_and_keyboard_tool>

人类可以用鼠标选取一段文字，然后：
- 复制这段文字到剪贴板。
- 把这段文字替换成另外一段文字。
- 删除这段文字（其实就是替换成空字符串）

把光标放在特定位置，然后：
- 粘贴
- 输出文字

系统给你设计了一套类似的工具，下面是一些使用例子（参数经过简化，完整参数见tool schema）：

复制某段内容，然后把它粘贴到某个内容的下方，然后在后面继续写新的内容：mouse_select(needle='content 1') + keyboard_copy + mouse_place_cursor(needle='content 2', direction='right') + keyboard_type('\n') + keyboard_paste + keyboard_type('new content')
从这个例子中，你可以注意到，在keyboard_type/paste完成之后，cursor的位置会自动放在输入/paste的内容的右边

把某段内容替换成另外一段内容：mouse_select(needle='some content') + keyboard_type('new content')

删除一段内容，并在后面另起一行输入新的内容：mouse_select(needle='some content') + keyboard_type('') + keyboard_type('\nnew_content')

在某个内容的前面一行打字：mouse_place_cursor(needle="some content 1",direction='left') + keyboard_type('some content 2\n')

</mouse_and_keyboard_tool>
```

- mouse_select
    - filepath
    - Needle: 要匹配的文本
    - Mode: [regex, literal] 。
    - allow_multi_apperance
- mouse_place_cursor
    - filepath
    - Needle 必须唯一。如果要编辑那种多行都有相似的，建议写 Python 脚本来解决。
    - direction: [left,right] 把cursor放在needle的左边还是右边
    - place_at_top_left 把光标放在第一行第一列。
    - place_at_bottom_right 把光标放在最后一行，最后一列。
- keyboard_copy
    - 必须已经有select的内容。执行成功后，cursor会取消选中
- keyboard_paste
    - 必须在这之前有copy操作
    - 必须放置了cursor
    - 执行成功后，cursor的位置会是在粘贴的内容的右边（同一行）
- keyboard_type
    - Content 想要输入的内容。需要输入\n来换行，\t来缩进等等。
    - 完成打字后，cursor的位置会是在输入内容的右边（同一行）
