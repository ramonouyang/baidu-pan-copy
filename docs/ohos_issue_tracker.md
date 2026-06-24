# 鸿蒙NEXT适配 - 问题跟踪文档

## 概述
- 项目：MediPulse 医脉
- 适配目标：鸿蒙NEXT (HarmonyOS NEXT)
- 创建日期：2026-06-24
- 文档状态：活跃

## 问题列表

### 1. [DTS2026062462949] SharedPreferences MissingPluginException on ohos
- **状态**：已解决
- **日期**：2026-06-24
- **根因**：SharedPreferences插件无ohos实现，调用getAll抛MissingPluginException导致初始化失败
- **修复方案**：为_getPrefs()添加try-catch，SharedPreferences不可用时优雅降级使用默认值
- **涉及文件**：`lib/services/user_service.dart, lib/services/llm_config_service.dart, lib/l10n/app_locale.dart`

### 2. [DTS2026062434015] sqflite_common_ffi不支持ohos平台
- **状态**：已解决
- **日期**：2026-06-24
- **根因**：sqflite_common_ffi抛Unsupported platform: ohos，数据库完全不可用
- **修复方案**：实现DatabaseWrapper(JSON文件存储)，提供sqflite兼容接口，DatabaseService在ohos下使用DatabaseWrapper
- **涉及文件**：`lib/platform/database_wrapper.dart, lib/services/database_service.dart`

### 3. [DTS2026062485601] path_provider无ohos实现
- **状态**：已解决
- **日期**：2026-06-24
- **根因**：path_provider抛MissingPluginException，无法获取应用数据目录
- **修复方案**：ohos平台使用硬编码路径/data/storage/el2/base/haps/entry/files/medipulse_db
- **涉及文件**：`lib/services/database_service.dart`

### 4. [DTS2026062426915] DatabaseService返回List<dynamic>导致类型不匹配
- **状态**：已解决
- **日期**：2026-06-24
- **根因**：database返回dynamic→db.query()返回dynamic→.map().toList()成为List<dynamic>不是List<User>
- **修复方案**：添加_query/_rawQuery/_insert/_update/_delete类型安全helper方法，显式转换List<Map<String, dynamic>>
- **涉及文件**：`lib/services/database_service.dart`

### 5. [DTS2026062487355] LoginPage._loadUsers()无try-catch导致白屏转圈
- **状态**：已解决
- **日期**：2026-06-24
- **根因**：UserService.getUsers()抛异常→_loading永远为true→无限转圈
- **修复方案**：添加try-catch：失败时设置_loading=false, _showRegister=true, 显示错误信息
- **涉及文件**：`lib/pages/auth/login_page.dart`

### 6. [DTS2026062479288] OHOS平台错误显示为屏幕英文文本
- **状态**：已解决
- **日期**：2026-06-24
- **根因**：NavigationChannel.notifyPageChanged和MethodChannel错误被Flutter错误边界捕获后显示在屏幕上
- **修复方案**：FlutterError.onError中过滤含MethodChannel#/notifyPageChanged/SetPresentInfo的错误
- **涉及文件**：`lib/main.dart`

### 7. [DTS2026062493757] Flutter 3.35 CardTheme重命名为CardThemeData
- **状态**：已解决
- **日期**：2026-06-24
- **根因**：Flutter 3.35将CardTheme重命名为CardThemeData，编译失败
- **修复方案**：将CardTheme改为CardThemeData，保持向后兼容
- **涉及文件**：`lib/theme/app_theme.dart`

### 8. [DTS2026062491532] syncfusion_flutter_pdf版本跨Flutter版本不兼容
- **状态**：已解决
- **日期**：2026-06-24
- **根因**：Flutter 3.29.3和3.35.8的pub解析不同版本syncfusion，无法统一
- **修复方案**：使用范围约束>=29.1.33 <32.0.0，两个平台都解析到31.1.19
- **涉及文件**：`pubspec.base.yaml`

### 9. [DTS2026062467326] intl版本跨Flutter版本不兼容
- **状态**：已解决
- **日期**：2026-06-24
- **根因**：Flutter 3.29.3需要intl 0.19.x，3.35.8需要intl 0.20.x
- **修复方案**：使用范围约束>=0.19.0 <0.21.0，两个平台各自解析兼容版本
- **涉及文件**：`pubspec.base.yaml`

### 10. [DTS2026062468758] database_wrapper.dart未使用的path_provider import
- **状态**：已解决
- **日期**：2026-06-24
- **根因**：database_wrapper.dart第4行有未使用的import path_provider，在ohos上可能触发加载失败
- **修复方案**：移除未使用的import
- **涉及文件**：`lib/platform/database_wrapper.dart`

### 11. [DTS2026062486600] BackupService初始化时SharedPreferences崩溃
- **状态**：已解决
- **日期**：2026-06-24
- **根因**：BackupService依赖SharedPreferences，在ohos上MissingPluginException导致初始化失败
- **修复方案**：BackupService初始化添加try-catch，失败时跳过并记录日志
- **涉及文件**：`lib/main.dart`

## 统计
- 总问题数：11
- 已解决：11
- 待解决：0
