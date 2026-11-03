"""Routing must distinguish absent inputs from requested implementation work."""
import tempfile
from pathlib import Path
import unittest

from support import runner


class ClassificationTests(unittest.TestCase):
    def test_absent_backend_and_design_do_not_start_figma(self):
        cases = [
            ('实现 Vue 计数器页面。没有设计稿，不声明 Figma 视觉还原；没有服务端/Mock/API/登录身份前置项。',
             'frontend', False),
            ('修复 Vue 页面，没有 Figma 设计稿。', 'frontend', False),
            ('开发 Vue 页面和 Java 服务端，按照 Figma 设计稿实现。', 'fullstack', True),
            ('实现 Vue 页面；不要改后端，但需要 Figma 设计稿还原。', 'frontend', True),
            ('# 非目标\n后端服务端和 Figma 设计稿\n# 目标\n实现 Vue 页面', 'frontend', False),
            ('本需求不涉及后端，只修改 Vue 页面', 'frontend', False),
            ('这不是后端需求，只修改 Vue 页面', 'frontend', False),
            ('后端接口不改，只改 Vue 页面', 'frontend', False),
            ('实现 Vue 页面，不存在 Figma 设计稿', 'frontend', False),
            ('实现 Java 服务端接口，不开发页面', 'backend', False),
            ('使用 JavaScript 实现 React 前端页面', 'frontend', False),
            ('修复浏览器缓存导致的 Vue 页面状态问题', 'frontend', False),
            ('实现前端缓存组件', 'frontend', False),
            ('不开发页面，开发 Java 服务端接口', 'backend', False),
            ('不要改前端，新增 Java 后端接口', 'backend', False),
            ('不是后端，是前端页面调整', 'frontend', False),
            ('不做 Figma 还原，开发 Vue 页面', 'frontend', False),
            ('不要改后端，按照 Figma 设计稿开发 Vue 页面', 'frontend', True),
            ('不开发后端，使用 Figma 设计稿实现 React 页面', 'frontend', True),
            ('后端返回错误时页面展示提示；不要修改后端', 'frontend', False),
            ('不要修改后端数据库，但新增 Java 服务端接口和 Vue 页面', 'fullstack', False),
            ('前端适配；后端接口需要修改，但数据库不改', 'fullstack', False),
            ('不要修改数据库，但后端新增接口并调整 Vue 页面', 'fullstack', False),
            ('后端需要新增接口，数据库不改，Vue 页面同步适配', 'fullstack', False),
            ('Java 服务端新增接口，数据库不改，Vue 页面适配', 'fullstack', False),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root/'request.md'
            for text, domain, design in cases:
                with self.subTest(text=text):
                    request.write_text(text)
                    result = runner.classify(request, root)
                    self.assertEqual(result['domain'], domain)
                    self.assertEqual(result['has_design'], design)
                    self.assertEqual(result['needs_browser_acceptance'], domain != 'backend')
                    self.assertIn('design_intent_decisions', result['evidence'])


if __name__ == '__main__':
    unittest.main()
