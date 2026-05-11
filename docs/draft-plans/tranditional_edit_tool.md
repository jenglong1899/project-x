replace_text
filepath:Path 支持相对路径或绝对路径。相对路径基于bash tool的cwd
mode:Literal['regex','literal']
needle:str 支持用 beginning-of-the-text.*?end-of-the-text 来选中一大段文字
repl:str 如果为空，效果等于删除
allow_multiple_apperance:bool=False

编辑失败的话，要把repl存到一个文件里面，然后再提供一个参数叫repl_from_file。这样编辑失败后，不用再重新把 repl 输出一遍。

insert_text
filepath:Path 支持相对路径或绝对路径。相对路径基于bash tool的cwd
needle:str 必须唯一。不一定需要输入一整行的内容，只要保证唯一就行了。
direction:Literal['before','after']
text:str

copy的话，让ai自己写代码，用正则表达式选中一大段文字，然后在选择粘贴到哪里去
