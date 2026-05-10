显示行号感觉还挺重要的吧。我有点忘了具体的理由了，反正就是感觉很重要。

```
nl -ba demos/temp.txt | sed -n '1,20p'
```
这种命令手敲起来还是感觉繁琐了，AI 可能会忘记要显示行号。

所以应该弄个工具，它默认就是显示行号的。

输入
filepath:Path 相对路径或绝对路径。
line_range_start:int | None min=1
line_range_end:int | None 如果为-1，表示阅读到文件末尾。否则必须大于 line_range_start
line_display:bool=True

输出
```
{filepath}:{line-range-start}-{line-range-end}
{文件内容}
```