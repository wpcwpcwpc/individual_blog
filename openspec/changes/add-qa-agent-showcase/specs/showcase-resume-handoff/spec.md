## ADDED Requirements

### Requirement: 稳定引用路径

对外引用的项目页路径 SHALL 为 `/projects/qa-agent`，该路径一经发布 MUST NOT 变更。任何重命名、重组 MUST 保证既有链接继续可访问（保留原路径或提供重定向）。

#### Scenario: 站点结构后续调整

- **WHEN** 站点因新增内容需要调整目录或分类
- **THEN** `/projects/qa-agent` 仍然可达，外部引用（简历、二维码）不失效

#### Scenario: 更换域名

- **WHEN** 域名发生更换
- **THEN** 旧域名提供跳转或保持解析，使已发出的简历链接仍可用

### Requirement: 简历文案

简历中的项目条目 SHALL 包含一行项目定位 + 链接，定位表述 SHALL 在 40 字以内说清"给谁解决什么问题"，MUST NOT 使用内部系统名与内部术语。

#### Scenario: 简历一句定位

- **WHEN** 撰写简历中的项目描述
- **THEN** 读者无需上下文即可大致理解该项目的用途，且点击链接可看到与描述一致的落地页

#### Scenario: 简历与落地页一致性

- **WHEN** 落地页内容被更新
- **THEN** 简历描述与落地页的核心定位仍然一致，不出现描述与页面不符

### Requirement: 二维码交付

纸质版简历 SHALL 附二维码指向 `/projects/qa-agent`。二维码生成 MUST NOT 把任何内部信息提交到第三方服务；生成产物 SHALL 存入仓库以便复用。

#### Scenario: 纸质简历扫码

- **WHEN** 读者用手机扫描简历上的二维码
- **THEN** 直达项目页且页面在手机端可读

#### Scenario: 二维码目标变更

- **WHEN** 需要更换二维码指向
- **THEN** 优先保持 URL 不变（改站点内容而非改链接），确需变更时重新生成并替换

### Requirement: PDF 兜底

站点 SHALL 提供项目内容的 PDF 导出，用于邮件附件与离线阅读场景。PDF 下载入口 SHALL 探测产物存在性，缺失时显示"生成中"提示而非指向不存在的文件。

#### Scenario: 邮件投递

- **WHEN** 需要以邮件附件形式投递材料
- **THEN** 可直接附上 PDF，接收方离线可读完整内容

#### Scenario: PDF 产物缺失

- **WHEN** PDF 尚未生成或导出失败
- **THEN** 页面显示生成中提示，主链接（项目页）仍完全可用

### Requirement: 联系渠道边界

对外展示的联系方式 SHALL 仅包含可公开渠道（邮箱、代码托管主页等）。MUST NOT 出现手机号、内部通讯账号、内部工单或知识库链接。

#### Scenario: about 页与 deck 末页

- **WHEN** 展示联系方式
- **THEN** 仅有可公开渠道，且这些渠道长期有效

#### Scenario: 读者索要更多细节

- **WHEN** 读者希望进一步沟通
- **THEN** 通过邮箱等公开渠道联系，站点不提供内部系统入口
