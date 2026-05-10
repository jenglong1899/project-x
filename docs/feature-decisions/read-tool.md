# Read Tool

显示行号的话，如果AI要给引用就方便多了。

```
nl -ba demos/temp.txt | sed -n '1,20p'
```

这种命令敲起来繁琐，导致AI也可能忘记显示行号？也还好吧，我看gpt就经常写那些比较复杂的 bash 命令。

如果确实麻烦的话，我们可以创建一个alias之类的东西
```
readfile <file_path> <line_start>
```

这个不是主要逻辑，可以先不做
