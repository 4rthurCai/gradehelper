import shutil

import pytest

from gradehelper.checks import code_quality as cq


def test_clean_matlab_has_no_issues():
    code = """function y = f(x)
if x > 0
    y = 1;
elseif x < 0
    y = -1;
else
    y = 0;
end
switch y
    case 1
        disp('pos')
    otherwise
        disp('other')
end
fprintf('%d\\n', y);
"""
    assert cq.check_matlab(code) == []


def test_matlab_issues_are_each_reported_once():
    code = """global counter
while true
    break
end
if a
    b = 1;
elseif c
    b = 2;
end
switch b
    case 1
        disp(1)
end
msg = sprintf('%d', x);
disp(msg)
"""
    assert cq.check_matlab(code) == [
        cq.GLOBAL_VARS,
        cq.WHILE_TRUE,
        cq.ELSEIF_NO_ELSE,
        cq.SWITCH_NO_OTHERWISE,
        cq.SPRINTF_DISP,
    ]


def test_matlab_keywords_in_comments_and_strings_are_ignored():
    code = "% elseif without else\ndisp('switch')\nx = input('');\n"
    assert cq.ELSEIF_NO_ELSE not in cq.check_matlab(code)
    assert cq.SWITCH_NO_OTHERWISE not in cq.check_matlab(code)


@pytest.mark.parametrize(
    "name, language, expected",
    [("ex1.m", "matlab", True), ("ex1.c", "matlab", False), ("ex1.h", "c", True),
     ("main.cpp", "cc", True), ("ex1.m", "c", False)],
)
def test_applies_to(name, language, expected):
    assert cq.applies_to(name, language) is expected


@pytest.mark.skipif(shutil.which("ctags") is None, reason="ctags not installed")
def test_cpp_using_namespace_and_globals(tmp_path):
    good = tmp_path / "good.cpp"
    good.write_text('// using namespace std;\nconst int N = 3;\nint main() { return N; }\n')
    bad = tmp_path / "bad.cpp"
    bad.write_text("using namespace std;\nint counter = 0;\nint main() { return counter; }\n")
    assert cq.check_file(good, "cc") == []
    assert cq.check_file(bad, "cc") == [cq.USING_NAMESPACE, cq.NON_CONST_VARS]

