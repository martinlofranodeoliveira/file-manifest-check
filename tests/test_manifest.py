import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import file_manifest_check as manifest


def encode(value):
    return (json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n').encode('utf-8')


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='manifest-public-')
        self.base=Path(self.temporary.name)
        self.root=self.base/'artifacts';self.root.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self,name,content):
        path=self.root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)

    def error(self,code,function,*args):
        with self.assertRaises(manifest.ManifestError) as caught:function(*args)
        self.assertEqual(caught.exception.code,code)
        self.assertEqual(str(caught.exception),code)

    def cli(self,*args,data=b'',closed_stdin=False):
        env={'PATH':'/usr/bin:/bin','PYTHONDONTWRITEBYTECODE':'1','PYTHONIOENCODING':'utf-8','PYTHONPATH':str(Path(manifest.__file__).parent),'LD_LIBRARY_PATH':str(Path(sys.base_prefix)/'lib')}
        options={'stdin':subprocess.DEVNULL,'preexec_fn':lambda:os.close(0)} if closed_stdin else {'input':data}
        return subprocess.run([sys.executable,'-B','-m','file_manifest_check',*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,timeout=4,**options)

    def test_empty_tree_is_a_small_content_only_manifest(self):
        (self.root/'empty-directory').mkdir()
        self.assertEqual(manifest.create_manifest(self.root),{'schema_version':1,'include_hidden':False,'files':[]})

    def test_known_digest_binary_empty_and_unicode_names(self):
        self.write('área com espaço/中.txt',b'abc');self.write('empty',b'');self.write('binary',b'\x00\xff')
        result=manifest.create_manifest(str(self.root))
        self.assertEqual([f['path'] for f in result['files']],['binary','empty','área com espaço/中.txt'])
        self.assertEqual(result['files'][-1],{'path':'área com espaço/中.txt','bytes':3,'sha256':'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad'})
        self.assertEqual(result['files'][0]['sha256'],hashlib.sha256(b'\x00\xff').hexdigest())
        self.assertEqual(result['files'][1]['bytes'],0)
        self.assertNotIn(str(self.root),json.dumps(result,ensure_ascii=False))

    def test_hidden_files_and_subtrees_are_pruned(self):
        self.write('public.txt',b'x');self.write('.env',b'synthetic');self.write('visible/.cache/data',b'ignored')
        (self.root/'.hidden-link').symlink_to(self.base/'missing')
        self.assertEqual([f['path'] for f in manifest.create_manifest(self.root)['files']],['public.txt'])

    def test_case_and_unicode_normalization_are_not_changed(self):
        for name in ['A','a','é','e\u0301']:self.write(name,b'x')
        self.assertEqual([f['path'] for f in manifest.create_manifest(self.root)['files']],['A','a','e\u0301','é'])

    def test_verification_accepts_dict_and_utf8_json_without_mutation(self):
        self.write('a',b'abc');snapshot=manifest.create_manifest(self.root);saved=copy.deepcopy(snapshot)
        for value in [snapshot,encode(snapshot),encode(snapshot).decode('utf-8')]:
            self.assertEqual(manifest.verify_manifest(self.root,value),{'schema_version':1,'ok':True,'missing':[],'extra':[],'changed':[]})
        self.assertEqual(snapshot,saved)

    def test_same_size_change_missing_and_extra_are_distinct(self):
        self.write('changed',b'old');self.write('missing',b'x');snapshot=manifest.create_manifest(self.root)
        (self.root/'missing').unlink();self.write('changed',b'new');self.write('extra',b'y')
        self.assertEqual(manifest.verify_manifest(self.root,snapshot),{'schema_version':1,'ok':False,'missing':['missing'],'extra':['extra'],'changed':['changed']})

    def test_malformed_snapshot_is_rejected_before_missing_root_access(self):
        bad={'schema_version':1,'include_hidden':False,'files':[{'path':'../outside','bytes':0,'sha256':'a'*64}]}
        self.error('INVALID_MANIFEST',manifest.verify_manifest,self.base/'missing',bad)
        self.error('INVALID_MANIFEST',manifest.verify_manifest,self.base/'missing',b'{"schema_version":1,"schema_version":1,"include_hidden":false,"files":[]}')
        self.error('INVALID_MANIFEST',manifest.verify_manifest,self.base/'missing',b'\xff')

    def test_bool_size_and_hidden_snapshot_path_are_invalid(self):
        for path,size in [('a',True),('.env',0),('a\\b',0),('/absolute',0)]:
            snapshot={'schema_version':1,'include_hidden':False,'files':[{'path':path,'bytes':size,'sha256':'a'*64}]}
            self.error('INVALID_MANIFEST',manifest.verify_manifest,self.root,snapshot)

    def test_visible_symlinks_and_regular_hardlinks_are_refused(self):
        self.write('a',b'abc');(self.root/'link').symlink_to(self.root/'a')
        self.error('UNSAFE_TREE',manifest.create_manifest,self.root);(self.root/'link').unlink()
        os.link(self.root/'a',self.base/'hardlink')
        self.error('UNSAFE_TREE',manifest.create_manifest,self.root)

    def test_cli_creates_compact_json_and_returns_difference_exit(self):
        self.write('a',b'old');r=self.cli('create',str(self.root))
        self.assertEqual(r.returncode,0);self.assertEqual(r.stderr,b'');snapshot=json.loads(r.stdout)
        self.assertEqual(r.stdout,encode(snapshot))
        self.write('a',b'new');r=self.cli('verify',str(self.root),data=encode(snapshot))
        self.assertEqual(r.returncode,1);self.assertEqual(json.loads(r.stdout)['changed'],['a']);self.assertEqual(r.stderr,b'')

    def test_cli_invalid_input_and_usage_do_not_echo_paths(self):
        r=self.cli('verify',str(self.base/'missing'),data=b'{bad')
        self.assertEqual((r.returncode,r.stdout,r.stderr),(2,b'',b'INVALID_MANIFEST\n'))
        r=self.cli('create',str(self.root),'--include-hidden')
        self.assertEqual((r.returncode,r.stdout,r.stderr),(2,b'',b'USAGE_ERROR\n'))

    def test_cli_special_file_fails_without_blocking(self):
        os.mkfifo(self.root/'synthetic-fifo')
        r=self.cli('create',str(self.root))
        self.assertEqual((r.returncode,r.stdout,r.stderr),(3,b'',b'UNSAFE_TREE\n'))

    def test_cli_closed_stdin_fails_with_fixed_io_error(self):
        r=self.cli('verify',str(self.root),closed_stdin=True)
        self.assertEqual((r.returncode,r.stdout,r.stderr),(3,b'',b'IO_ERROR\n'))


if __name__=='__main__':unittest.main()
