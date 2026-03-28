应该把bash工具重命名为shell工具
read工具还是有点太限制了
用shell命令可以做到很灵活

- 找出所有二级标题
```
rg -n "^## " xx.md
```

- 功能：从某个二级标题 开始，一直读到 下一个同级或更高级标题出现之前
```
awk '/^## 二级标题1/{flag=1;print;next} /^## /{flag=0} flag' xx.md
```

所以应该给shell工具加一个参数，content limit percent:float(min=0.1,max=100,default=5)，超过这个字数，剩余内容放到一个文件中，并告知总共超了多少。文件名要以对应的tool-call-id命名
