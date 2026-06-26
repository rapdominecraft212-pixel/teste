#!/usr/bin/env python3
"""Teste do AccountPool — verifica login automatico e criacao de pagina."""

import asyncio
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from Playwright.qwen_account_pool import QwenAccount, load_accounts_config


async def test_login():
    """Testa login de uma conta unica."""
    config = load_accounts_config()
    print(f"\n=== Teste de Login ===")
    print(f"Conta: {config[0]['email']}")

    acc = QwenAccount("test_1", config[0]["email"], config[0]["password"], headless=True)

    print(f"\n[Warm-up] Fazendo login...")
    t0 = time.time()
    await acc.warm_up()
    dt = time.time() - t0
    print(f"[Warm-up] Login OK em {dt:.1f}s — estado: {acc.state}")

    # Criar pagina de teste
    print(f"\n[Teste] Criando pagina de trabalho...")
    page = await acc.new_page(tag="teste")
    print(f"[Teste] Pagina criada! URL: {page.url}")

    # Verificar se textarea esta visivel (sessao ativa)
    textarea = await page.query_selector("textarea")
    print(f"[Teste] Textarea encontrada: {textarea is not None}")

    # Fechar pagina
    await acc.close_page(page)
    print(f"[Teste] Pagina fechada (browser continua aberto)")

    # Criar segunda pagina (deve ser instantaneo — sem login)
    print(f"\n[Teste 2] Criando segunda pagina (deve ser instantaneo)...")
    t0 = time.time()
    page2 = await acc.new_page(tag="teste2")
    dt2 = time.time() - t0
    print(f"[Teste 2] Segunda pagina criada em {dt2:.1f}s")
    await acc.close_page(page2)

    # Shutdown
    print(f"\n[Shutdown] Fechando conta...")
    await acc.shutdown()
    print(f"[Shutdown] Conta fechada. Estado: {acc.state}")


async def test_pool():
    """Testa o pool completo com 1 conta."""
    from Playwright.qwen_account_pool import AccountPool

    config = load_accounts_config()
    print(f"\n=== Teste do Pool ===")
    print(f"Contas: {len(config)}")

    pool = AccountPool.initialize(config, headless=True)
    print(f"Pool pronto! {pool.ready_count}/{pool.total_accounts} contas")
    print(f"Max jobs simultaneos: {pool.max_concurrent_jobs}")

    # Adquirir conta
    print(f"\n[Acquire] Pegando conta do pool...")
    conta = pool.acquire(timeout=30)
    print(f"[Acquire] Conta {conta.id} adquirida (estado: {conta.state})")

    # Criar pagina via pool
    print(f"\n[Page] Criando pagina via pool.run_async()...")
    page = pool.run_async(conta.new_page(tag="pool_test"))
    print(f"[Page] Pagina criada! URL: {page.url}")

    # Fechar pagina
    pool.run_async(conta.close_page(page))
    print(f"[Page] Pagina fechada")

    # Devolver conta
    pool.release(conta)
    print(f"\n[Release] Conta {conta.id} devolvida ao pool")

    # Shutdown
    pool.shutdown()
    print(f"[Shutdown] Pool desligado")


if __name__ == "__main__":
    print("Escolha o teste:")
    print("1 - Login de uma conta")
    print("2 - Pool completo")
    escolha = input("Opcao (1/2): ").strip()

    if escolha == "1":
        asyncio.run(test_login())
    elif escolha == "2":
        test_pool()  # Pool roda seu proprio event loop
    else:
        print("Opcao invalida")
