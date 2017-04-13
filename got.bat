@echo off
REG QUERY "HKCU\Software\Python\PythonCore\3.6-32\InstallPath" > nul 2> nul
if %ERRORLEVEL% neq 0 goto trysixtyfourbit

rem http://stackoverflow.com/a/13674631/309308 -- Not sure how consistent this is
for /F "usebackq tokens=3*" %%A IN (`REG QUERY "HKCU\Software\Python\PythonCore\3.6-32\InstallPath" /ve`) DO (
	set PYTHON_DIR=%%A %%B
)

"%PYTHON_DIR%\python" "%~dp0\got" %*
set GOTERR=%ERRORLEVEL%
goto end

:trysixtyfourbit
REG QUERY "HKCU\Software\Python\PythonCore\3.6\InstallPath" > nul 2> nul
if %ERRORLEVEL% neq 0 goto fail

rem http://stackoverflow.com/a/13674631/309308 -- Not sure how consistent this is
for /F "usebackq tokens=3*" %%A IN (`REG QUERY "HKCU\Software\Python\PythonCore\3.6\InstallPath" /ve`) DO (
	set PYTHON_DIR=%%A %%B
)

"%PYTHON_DIR%\python" "%~dp0\got" %*
set GOTERR=%ERRORLEVEL%
goto end

:fail
echo Unable to find Python 3.6 in the registry
:end

exit /B %GOTERR%
