import subprocess
import click
import sys

import webbrowser
import threading
import time

@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """🐙 Jason Octopus (AlphaForge) CLI"""
    if ctx.invoked_subcommand is None:
        ctx.invoke(serve)

@cli.command()
def demo():
    """파이프라인 통합 데모 스크립트를 실행합니다."""
    click.echo("🚀 AlphaForge 데모 실행 중...")
    subprocess.run(["python", "run_demo.py"])

def open_browser():
    time.sleep(1.5)
    webbrowser.open("http://127.0.0.1:8080/")

@cli.command()
def serve():
    """FastAPI 리포트 서버를 실행합니다."""
    click.echo("🌐 웹 서버 시작 (http://127.0.0.1:8080/)")
    threading.Thread(target=open_browser, daemon=True).start()
    subprocess.run([sys.executable, "-m", "uvicorn", "backend.main:app", "--reload", "--host", "0.0.0.0", "--port", "8080"])

@cli.command()
def test():
    """pytest 기반 테스트를 구동합니다."""
    click.echo("🧪 테스트 구동 중...")
    subprocess.run(["pytest", "tests/", "-v"])

if __name__ == "__main__":
    cli()
