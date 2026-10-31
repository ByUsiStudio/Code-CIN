# codecin/lib —— 内置标准库（pic 包形式）

这里同时是 CIN 的 41 个内置标准库和它们的 pic 包定义：

```text
codecin/lib/
├── math/
│   ├── pic.toml
│   └── src/math.cin
├── str/
│   ├── pic.toml
│   └── src/str.cin
└── ...
```

运行时 `import "math.cin"` / `import "lib/math.cin"` 解析到
`codecin/lib/math/src/math.cin`，`import "math"` 也可以；每个目录可独立
`pic build` / `pic install`。

## 修改与校验

- 直接编辑 `codecin/lib/<name>/src/<name>.cin`；
- `pic.toml` 由脚本按 `codecin/__init__.py` 的版本与源文件头注释生成，**不要手工编辑**；
- `python script/sync_stdlib_packages.py` 重新生成 `pic.toml`；
- `python script/sync_stdlib_packages.py --check` 校验包结构与版本（CI 使用）；
- `python script/sync_stdlib_packages.py --build` 构建全部 `.cinpkg`。

## 构建与安装

```bash
pic build --manifest codecin/lib/math
# 等价写法
python -m codecin.pic build --manifest codecin/lib/math

pic install ./codecin/lib/math --project
```

`pic build` 默认把归档写到包内的 `dist/`；脚本的 `--build` 统一写到仓库根
`dist/stdlib-packages/<name>-<version>.cinpkg`。两处构建产物都已被 `.gitignore` 忽略。

## 发布

41 个包的 `pic.toml` 统一声明 `repo = "https://gitee.com/codecin/internal-libraries.git"`，并已发布到
`package.codecin.org.cn`（版本 6.4.2）。重新发布单个包：

```bash
python -m codecin.pic publish --manifest codecin/lib/<name> --no-tag
```

注册表会绑定包首次发布时的仓库地址；如果包已绑定到其它仓库，需要先删除再发布。
