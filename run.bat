@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
C:\Users\Chen\.conda\envs\novelia-searcher\python.exe main.py
pause
