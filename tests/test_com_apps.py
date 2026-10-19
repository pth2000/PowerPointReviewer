"""多实例发现只读取已运行文稿，并按 COM 身份去重。"""

from pathlib import Path
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from app.presentation import com as com_apps


def application(library='{44720440-94BF-4940-926D-4F38FECF2A48}', identity=None):
    app = Mock(Name='Microsoft PowerPoint')
    app.Presentations.Count = 1
    app.SlideShowWindows.Count = 1
    app._oleobj_.QueryInterface.return_value = identity if identity is not None else object()
    app._oleobj_.GetTypeInfo.return_value.GetContainingTypeLib.return_value = [
        NS(GetLibAttr=lambda: (library,))]
    return app


class RunningApplicationsTests(unittest.TestCase):
    def setUp(self):
        import pythoncom
        self.pythoncom = pythoncom
        self.rot = Mock()
        patcher = patch('pythoncom.GetRunningObjectTable', return_value=self.rot)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch('win32com.client.dynamic.Dispatch', side_effect=lambda obj: obj)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.monikers = []
        self.rot.EnumRunning.side_effect = lambda: iter(self.monikers)

    def add_document(self, app, name='D:/lesson.pptx'):
        moniker = Mock()
        moniker.IsSystemMoniker.return_value = self.pythoncom.MKSYS_FILEMONIKER
        moniker.GetDisplayName.return_value = name
        document = NS(Application=app, FullName=str(Path(name)))
        obj = Mock()
        obj.QueryInterface.return_value = document
        mapping = getattr(self, 'objects', {})
        mapping[moniker] = obj
        self.objects = mapping
        self.rot.GetObject.side_effect = mapping.__getitem__
        self.monikers.append(moniker)
        return moniker

    def test_idle_global_instance_does_not_hide_manual_wps_show(self):
        idle, actual = application(), application()
        idle.Presentations.Count = idle.SlideShowWindows.Count = 0
        self.add_document(actual)
        def active(prog):
            if prog == 'Kwpp.Application':
                return idle
            raise OSError('not running')
        with patch('win32com.client.GetActiveObject', side_effect=active), \
             patch('win32com.client.DispatchEx') as create:
            result = list(com_apps.running_applications())
        self.assertEqual(result, [('Kwpp.Application', 'WPS 演示', idle),
                                  ('Kwpp.Application', 'WPS 演示', actual)])
        create.assert_not_called()

    def test_same_com_instance_from_multiple_documents_is_returned_once(self):
        identity = object()
        first, second = application(identity=identity), application(identity=identity)
        self.add_document(first)
        self.add_document(second, 'D:/another.pptx')
        with patch('win32com.client.GetActiveObject', side_effect=OSError()):
            self.assertEqual(list(com_apps.running_applications()),
                             [('Kwpp.Application', 'WPS 演示', first)])

    def test_global_instance_and_its_document_are_deduplicated(self):
        app = application()
        self.add_document(app)
        with patch('win32com.client.GetActiveObject', return_value=app):
            self.assertEqual(list(com_apps.running_applications()),
                             [('PowerPoint.Application', 'PowerPoint', app)])

    def test_type_library_distinguishes_identical_compatibility_names(self):
        wps = application()
        ppt = application('{91493440-5A91-11CF-8700-00AA0060263B}')
        self.add_document(wps)
        self.add_document(ppt, 'D:/second.pptx')
        self.assertEqual([(p, label) for p, label, _ in com_apps._rot_applications()],
                         list(reversed(com_apps.PROG_IDS)))

    def test_unrelated_objects_and_stale_entries_do_not_block_later_document(self):
        app = application()
        bad = self.add_document(app, 'D:/gone.pptx')
        self.objects[bad].QueryInterface.side_effect = OSError('closed')
        self.add_document(application('unknown'), 'D:/unknown.pptx')
        self.add_document(app, 'D:/sheet.xlsx')
        self.add_document(app)
        self.assertEqual(list(com_apps._rot_applications()), [('Kwpp.Application', 'WPS 演示', app)])

    def test_mismatched_document_registration_is_rejected(self):
        app = application()
        moniker = self.add_document(app)
        self.objects[moniker].QueryInterface.return_value.FullName = 'D:/other.pptx'
        self.assertEqual(list(com_apps._rot_applications()), [])

    def test_failed_rot_leaves_global_instance_available(self):
        app = application()
        with patch('pythoncom.GetRunningObjectTable', side_effect=OSError()), \
             patch('win32com.client.GetActiveObject', return_value=app):
            self.assertEqual(len(list(com_apps.running_applications())), 1)


if __name__ == '__main__':
    unittest.main()
