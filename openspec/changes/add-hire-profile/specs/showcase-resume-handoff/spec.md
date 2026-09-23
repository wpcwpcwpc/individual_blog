## MODIFIED Requirements

### Requirement: 稳定引用路径

对外引用的项目页路径 SHALL 为 `/projects/qa-agent`，该路径一经发布 MUST NOT 变更。简历侧对外引用的站点入口 SHALL 为 `/resume`（文本形态）与 `/resume/pdf`（PDF 形态），两者同样一经发布 MUST NOT 变更。任何重命名、重组 MUST 保证既有链接继续可访问（保留原路径或提供重定向）。

#### Scenario: 站点结构后续调整

- **WHEN** 站点因新增内容需要调整目录或分类
- **THEN** `/projects/qa-agent`、`/resume` 与 `/resume/pdf` 仍然可达，外部引用（简历、二维码）不失效

#### Scenario: 更换域名

- **WHEN** 域名发生更换
- **THEN** 旧域名提供跳转或保持解析，使已发出的简历链接仍可用

#### Scenario: 简历形态调整

- **WHEN** 需要改变 `/resume` 的呈现形态（如调整区块顺序、增删分节）
- **THEN** 路径与对外可达性保持不变，仅页面内容变化

### Requirement: 联系渠道边界

对外展示的联系方式 SHALL 仅包含可公开渠道（邮箱、代码托管主页等）。MUST NOT 出现手机号、内部通讯账号、内部工单或知识库链接。首页身份区、关于页、简历文本页与 deck 末页展示的联系方式 SHALL 同源（来自站点配置与履历单一真源），改一处即全站生效。

#### Scenario: about 页与 deck 末页

- **WHEN** 展示联系方式
- **THEN** 仅有可公开渠道，且这些渠道长期有效

#### Scenario: 首页身份区与简历文本页

- **WHEN** 招聘方在首页或简历文本页寻找联系方式
- **THEN** 可见邮箱与代码托管主页，MUST NOT 出现手机号或内部系统入口

#### Scenario: 读者索要更多细节

- **WHEN** 读者希望进一步沟通
- **THEN** 通过邮箱等公开渠道联系，站点不提供内部系统入口
