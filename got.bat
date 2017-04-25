@echo off
set KEY_CATEGORY=HKCU
set NEXT_LABEL=tryhklm

:findpython
REG QUERY "%KEY_CATEGORY%\Software\Python\PythonCore\3.6-32\InstallPath" > nul 2> nul
if %ERRORLEVEL% neq 0 goto trysixtyfourbit

rem http://stackoverflow.com/a/13674631/309308 -- Not sure how consistent this is
for /F "usebackq tokens=3*" %%A IN (`REG QUERY "%KEY_CATEGORY%\Software\Python\PythonCore\3.6-32\InstallPath" /ve`) DO (
	set PYTHON_DIR=%%A %%B
)

"%PYTHON_DIR%\python" "%~dp0\got" %*
set GOTERR=%ERRORLEVEL%
goto end

:trysixtyfourbit
REG QUERY "%KEY_CATEGORY%\Software\Python\PythonCore\3.6\InstallPath" > nul 2> nul
if %ERRORLEVEL% neq 0 goto %NEXT_LABEL%

rem http://stackoverflow.com/a/13674631/309308 -- Not sure how consistent this is
for /F "usebackq tokens=3*" %%A IN (`REG QUERY "%KEY_CATEGORY%\Software\Python\PythonCore\3.6\InstallPath" /ve`) DO (
	set PYTHON_DIR=%%A %%B
)

"%PYTHON_DIR%\python" "%~dp0\got" %*
set GOTERR=%ERRORLEVEL%
goto end

:tryhklm
set KEY_CATEGORY=HKLM
set NEXT_LABEL=fail
goto findpython

:fail
echo Unable to find Python 3.6 in the registry
set GOTERR=2
:end

exit /B %GOTERR%
