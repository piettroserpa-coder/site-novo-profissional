from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response
import sqlite3
import csv
import io
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = "chave_secreta_vendas_premium"

# Definição da Meta Mensal de Faturamento
META_MENSAL = 50000.00

# Taxas de Meios de Pagamento (Configurável)
TAXAS_PAGAMENTO = {
    'Dinheiro': 0.0,
    'Pix': 0.005,          # 0.5%
    'Cartão de Débito': 0.015, # 1.5%
    'Cartão de Crédito': 0.035 # 3.5%
}

# -------------------------------------------------------------------------
# BANCO DE DADOS: Estrutura Atualizada e Correção de Colunas
# -------------------------------------------------------------------------
def conectar_db():
    conn = sqlite3.connect('sistema_vendas.db')
    conn.row_factory = sqlite3.Row
    return conn

with conectar_db() as conn:
    # 1. Usuários e Cargos
    conn.execute('''CREATE TABLE IF NOT EXISTS usuarios (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        username TEXT UNIQUE, 
                        senha TEXT, 
                        cargo TEXT)''')
    
    # 2. Clientes
    conn.execute('''CREATE TABLE IF NOT EXISTS clientes (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        nome TEXT, 
                        email TEXT, 
                        cpf TEXT, 
                        telefone TEXT, 
                        endereco TEXT, 
                        data_nascimento TEXT, 
                        estado_civil TEXT, 
                        genero TEXT)''')

    # 3. Fornecedores
    conn.execute('''CREATE TABLE IF NOT EXISTS fornecedores (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        nome TEXT, 
                        contato TEXT)''')

    # 4. Produtos
    conn.execute('''CREATE TABLE IF NOT EXISTS produtos (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        nome TEXT, 
                        preco_custo REAL, 
                        preco REAL, 
                        estoque INTEGER, 
                        quantidade_minima INTEGER, 
                        lucro REAL, 
                        fornecedor_id INTEGER)''')

    # 5. Vendas
    conn.execute('''CREATE TABLE IF NOT EXISTS vendas (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        cliente_id INTEGER, 
                        produto_id INTEGER, 
                        quantidade INTEGER, 
                        valor_total REAL, 
                        forma_pagamento TEXT, 
                        desconto REAL, 
                        data_venda TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    # 6. Despesas (Fluxo de Caixa com Coluna CATEGORIA adicionada)
    conn.execute('''CREATE TABLE IF NOT EXISTS despesas (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, 
                        descricao TEXT, 
                        valor REAL, 
                        categoria TEXT,
                        data_gasto TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')

    # Correções preventivas / Migrações de colunas para bancos existentes
    try:
        conn.execute("ALTER TABLE usuarios ADD COLUMN username TEXT")
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("ALTER TABLE produtos ADD COLUMN fornecedor_id INTEGER")
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("ALTER TABLE vendas ADD COLUMN forma_pagamento TEXT DEFAULT 'Dinheiro'")
    except sqlite3.OperationalError:
        pass
        
    try:
        conn.execute("ALTER TABLE vendas ADD COLUMN desconto REAL DEFAULT 0.0")
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute("ALTER TABLE despesas ADD COLUMN categoria TEXT DEFAULT 'Outros'")
    except sqlite3.OperationalError:
        pass

    # Geração dos hashes para os usuários padrão (Segurança RBAC)
    senha_hash_admin = generate_password_hash('1234')
    senha_hash_vendedor = generate_password_hash('1234')

    # Inserção segura: não sobrescreve caso o usuário mude a senha posteriormente
    conn.execute("INSERT OR IGNORE INTO usuarios (id, username, senha, cargo) VALUES (1, 'admin', ?, 'admin')", (senha_hash_admin,))
    conn.execute("INSERT OR IGNORE INTO usuarios (id, username, senha, cargo) VALUES (2, 'vendedor', ?, 'vendedor')", (senha_hash_vendedor,))
    conn.commit()


# -------------------------------------------------------------------------
# DECORATORS DE SEGURANÇA (RBAC)
# -------------------------------------------------------------------------
def requer_login(f):
    @wraps(f)
    def decorada(*args, **kwargs):
        if 'usuario_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorada

def requer_admin(f):
    @wraps(f)
    def decorada(*args, **kwargs):
        if session.get('usuario_cargo') != 'admin':
            flash("Acesso Negado: Esta rota requer privilégios de Administrador.")
            return "Acesso Negado. Apenas administradores podem acessar esta página.", 403
        return f(*args, **kwargs)
    return decorada


# -------------------------------------------------------------------------
# ROTAS DE AUTENTICAÇÃO
# -------------------------------------------------------------------------
@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        usuario = request.form['usuario'].strip().lower()
        senha = request.form['senha'].strip()
        
        conn = conectar_db()
        user_db = conn.execute("SELECT * FROM usuarios WHERE username = ?", (usuario,)).fetchone()
        conn.close()
        
        # Validação segura usando check_password_hash
        if user_db and check_password_hash(user_db['senha'], senha):
            session['usuario_id'] = user_db['id']
            session['usuario_nome'] = user_db['username']
            session['usuario_cargo'] = user_db['cargo']
            
            if user_db['cargo'] == 'vendedor':
                return redirect(url_for('vendas'))
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', erro="Usuário ou senha incorretos!")
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


# -------------------------------------------------------------------------
# DASHBOARD PRINCIPAL (Com Alerta de CRM)
# -------------------------------------------------------------------------
@app.route('/dashboard')
@requer_login
@requer_admin
def dashboard():
    conn = conectar_db()
    total_vendas = conn.execute("SELECT SUM(valor_total) FROM vendas").fetchone()[0] or 0.0
    total_clientes = conn.execute("SELECT COUNT(id) FROM clientes").fetchone()[0] or 0
    total_estoque = conn.execute("SELECT SUM(estoque) FROM produtos").fetchone()[0] or 0
    
    # Alertas de Estoque Mínimo
    alertas_estoque = conn.execute("""
        SELECT p.id, p.nome, p.estoque, p.quantidade_minima, f.nome AS fornecedor_nome, f.contato AS fornecedor_contato
        FROM produtos p
        LEFT JOIN fornecedores f ON p.fornecedor_id = f.id
        WHERE p.estoque <= p.quantidade_minima
    """).fetchall()
    
    # Últimas Vendas Realizadas
    ultimas_atividades = conn.execute("""
        SELECT v.data_venda, p.nome AS produto_nome, v.valor_total
        FROM vendas v
        JOIN produtos p ON v.produto_id = p.id
        ORDER BY v.data_venda DESC LIMIT 5
    """).fetchall()

    # CRM: Alerta de Aniversariantes do Mês Atual
    mes_atual = datetime.now().strftime('%m')
    aniversariantes = conn.execute("""
        SELECT nome, data_nascimento, telefone, email 
        FROM clientes 
        WHERE strftime('%m', data_nascimento) = ?
    """, (mes_atual,)).fetchall()
    
    conn.close()
    
    return render_template('index.html', 
                           total_vendas=total_vendas, 
                           total_clientes=total_clientes, 
                           total_estoque=total_estoque, 
                           atividades=ultimas_atividades, 
                           alertas=alertas_estoque,
                           aniversariantes=aniversariantes)


# -------------------------------------------------------------------------
# CADASTROS: CLIENTES, FORNECEDORES E PRODUTOS
# -------------------------------------------------------------------------
@app.route('/clientes', methods=['GET', 'POST'])
@requer_login
def clientes():
    conn = conectar_db()
    if request.method == 'POST':
        conn.execute("""
            INSERT INTO clientes (nome, email, cpf, telefone, endereco, data_nascimento, estado_civil, genero) 
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (request.form['nome'], request.form['email'], request.form['cpf'], request.form['telefone'], 
              request.form['endereco'], request.form['data_nascimento'], request.form['estado_civil'], request.form['genero']))
        conn.commit()
        flash("Cliente cadastrado com sucesso!")
        return redirect(url_for('clientes'))
        
    lista = conn.execute("SELECT * FROM clientes").fetchall()
    conn.close()
    return render_template('clientes.html', clientes=lista)

@app.route('/api/cliente/<int:id_cliente>')
@requer_login
def api_cliente(id_cliente):
    conn = conectar_db()
    cliente = conn.execute("SELECT nome FROM clientes WHERE id = ?", (id_cliente,)).fetchone()
    if not cliente:
        conn.close()
        return jsonify({"erro": "Cliente não encontrado"}), 404
        
    compras_db = conn.execute("""
        SELECT v.data_venda, p.nome AS produto_nome, v.quantidade, v.valor_total
        FROM vendas v
        JOIN produtos p ON v.produto_id = p.id
        WHERE v.cliente_id = ?
        ORDER BY v.data_venda DESC
    """, (id_cliente,)).fetchall()
    
    compras = []
    total_gasto = 0.0
    for c in compras_db:
        compras.append({
            "data": datetime.strptime(c["data_venda"], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y"),
            "produto": c["produto_nome"],
            "qtd": c["quantidade"],
            "total": c["valor_total"]
        })
        total_gasto += c["valor_total"]
        
    conn.close()
    return jsonify({
        "cliente_nome": cliente["nome"],
        "total_gasto": total_gasto,
        "compras": compras
    })

@app.route('/fornecedores', methods=['GET', 'POST'])
@requer_login
@requer_admin
def fornecedores():
    conn = conectar_db()
    if request.method == 'POST':
        conn.execute("INSERT INTO fornecedores (nome, contato) VALUES (?, ?)", (request.form['nome'], request.form['contato']))
        conn.commit()
        flash("Fornecedor adicionado!")
        return redirect(url_for('fornecedores'))
        
    lista = conn.execute("SELECT * FROM fornecedores").fetchall()
    conn.close()
    return render_template('fornecedores.html', fornecedores=lista)

@app.route('/produtos', methods=['GET', 'POST'])
@requer_login
@requer_admin
def produtos():
    conn = conectar_db()
    if request.method == 'POST':
        pc, p = float(request.form['preco_custo']), float(request.form['preco'])
        f_id = request.form.get('fornecedor_id')
        f_id = int(f_id) if f_id else None
        
        conn.execute("""
            INSERT INTO produtos (nome, preco_custo, preco, estoque, quantidade_minima, lucro, fornecedor_id) 
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (request.form['nome'], pc, p, int(request.form['estoque']), int(request.form['quantidade_minima']), p - pc, f_id))
        conn.commit()
        flash("Produto cadastrado com sucesso!")
        return redirect(url_for('produtos'))
        
    lista = conn.execute("""
        SELECT p.*, f.nome AS fornecedor_nome
        FROM produtos p
        LEFT JOIN fornecedores f ON p.fornecedor_id = f.id
    """).fetchall()
    
    lista_fornecedores = conn.execute("SELECT id, nome FROM fornecedores").fetchall()
    conn.close()
    return render_template('produtos.html', produtos=lista, fornecedores=lista_fornecedores)


# -------------------------------------------------------------------------
# SISTEMA DE CHECKOUT DINÂMICO (CARRINHO MULTIPRODUTOS)
# -------------------------------------------------------------------------
@app.route('/vendas')
@requer_login
def vendas():
    conn = conectar_db()
    c = conn.execute("SELECT id, nome FROM clientes").fetchall()
    p = conn.execute("SELECT id, nome, preco, estoque FROM produtos").fetchall()
    conn.close()
    
    carrinho = session.get('carrinho', [])
    total = sum(item['subtotal'] for item in carrinho)
    return render_template('vendas.html', clientes=c, produtos=p, carrinho=carrinho, total=total)

@app.route('/vendas/adicionar', methods=['POST'])
def vendas_adicionar():
    p_id, qtd = int(request.form['produto_id']), int(request.form['quantidade'])
    conn = conectar_db()
    prod = conn.execute("SELECT * FROM produtos WHERE id = ?", (p_id,)).fetchone()
    conn.close()
    
    if prod and prod['estoque'] >= qtd:
        carrinho = session.get('carrinho', [])
        
        # Se o item já existir no carrinho, incrementa a quantidade de forma dinâmica
        item_existe = False
        for item in carrinho:
            if item['id'] == prod['id']:
                if prod['estoque'] >= (item['quantidade'] + qtd):
                    item['quantidade'] += qtd
                    item['subtotal'] = item['quantidade'] * item['preco']
                    item_existe = True
                else:
                    flash(f"Estoque insuficiente para adicionar mais {qtd} unidade(s)!")
                    return redirect(url_for('vendas'))
                break
        
        if not item_existe:
            carrinho.append({
                'id': prod['id'], 
                'nome': prod['nome'], 
                'preco': prod['preco'], 
                'quantidade': qtd, 
                'subtotal': prod['preco'] * qtd
            })
            
        session['carrinho'] = carrinho
        session['cliente_venda_id'] = request.form.get('cliente_id')
    else:
        flash("Estoque insuficiente!")
        
    return redirect(url_for('vendas'))

# Remoção individual de itens no carrinho baseado no índice da lista ou ID
@app.route('/vendas/remover/<int:item_id>')
@requer_login
def vendas_remover(item_id):
    carrinho = session.get('carrinho', [])
    # Filtra o carrinho removendo o produto selecionado
    novo_carrinho = [item for item in carrinho if item['id'] != item_id]
    session['carrinho'] = novo_carrinho
    flash("Item removido do carrinho.")
    return redirect(url_for('vendas'))

@app.route('/vendas/finalizar', methods=['POST'])
def vendas_finalizar():
    carrinho = session.get('carrinho', [])
    cliente_id = request.form.get('cliente_id') or session.get('cliente_venda_id')
    forma_pagto = request.form.get('forma_pagamento', 'Dinheiro')
    desconto = float(request.form.get('desconto', 0) or 0)
    
    if not carrinho or not cliente_id:
        flash("Carrinho vazio ou cliente não selecionado!")
        return redirect(url_for('vendas'))
        
    conn = conectar_db()
    for item in carrinho:
        # Rateia proporcionalmente o desconto total inserido entre os itens
        valor_com_desconto = item['subtotal'] - (desconto / len(carrinho))
        
        conn.execute("""
            INSERT INTO vendas (cliente_id, produto_id, quantidade, valor_total, forma_pagamento, desconto) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, (cliente_id, item['id'], item['quantidade'], max(0, valor_com_desconto), forma_pagto, desconto))
        
        # Deduz fisicamente a quantidade vendida do estoque do produto
        conn.execute("UPDATE produtos SET estoque = estoque - ? WHERE id = ?", (item['quantidade'], item['id']))
        
    conn.commit()
    conn.close()
    
    session.pop('carrinho', None)
    session.pop('cliente_venda_id', None)
    flash("Venda finalizada com sucesso!")
    return redirect(url_for('vendas'))

@app.route('/vendas/limpar')
def vendas_limpar():
    session.pop('carrinho', None)
    session.pop('cliente_venda_id', None)
    return redirect(url_for('vendas'))


# -------------------------------------------------------------------------
# GESTÃO FINANCEIRA: DESPESAS CATEGORIZADAS
# -------------------------------------------------------------------------
@app.route('/despesas', methods=['GET', 'POST'])
@requer_login
@requer_admin
def despesas():
    conn = conectar_db()
    if request.method == 'POST':
        conn.execute("""
            INSERT INTO despesas (descricao, valor, categoria) 
            VALUES (?, ?, ?)
        """, (request.form['descricao'], float(request.form['valor']), request.form['categoria']))
        conn.commit()
        flash("Despesa categorizada registrada!")
        return redirect(url_for('despesas'))
        
    lista = conn.execute("SELECT * FROM despesas ORDER BY data_gasto DESC").fetchall()
    conn.close()
    return render_template('despesas.html', despesas=lista)


# -------------------------------------------------------------------------
# RELATÓRIOS E INTELIGÊNCIA COM CRM E EXPORTAÇÃO CSV
# -------------------------------------------------------------------------
@app.route('/relatorios')
@requer_login
@requer_admin
def relatorios():
    periodo = request.args.get('periodo', 'todos')
    condicao_data = "1=1"
    condicao_despesa = "1=1"
    parametros_data = []
    hoje = datetime.now()
    
    if periodo != 'todos':
        if periodo == 'hoje':
            data_limite = hoje.strftime('%Y-%m-%d 00:00:00')
        elif periodo == '7_dias':
            data_limite = (hoje - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
        elif periodo == 'este_mes':
            data_limite = hoje.strftime('%Y-%m-01 00:00:00')
            
        condicao_data = "v.data_venda >= ?"
        condicao_despesa = "data_gasto >= ?"
        parametros_data.append(data_limite)
        
    conn = conectar_db()
    
    # Meta Mensal
    inicio_mes_atual = hoje.strftime('%Y-%m-01 00:00:00')
    faturamento_mes_atual = conn.execute(
        "SELECT SUM(valor_total) FROM vendas WHERE data_venda >= ?", (inicio_mes_atual,)
    ).fetchone()[0] or 0.0
    
    porcentagem_meta = (faturamento_mes_atual / META_MENSAL) * 100
    porcentagem_barra = min(porcentagem_meta, 100.0)
    
    # Coleta de dados das vendas baseada no período
    vendas_db = conn.execute(f"""
        SELECT v.*, c.nome AS cliente_nome, p.nome AS produto_nome, p.preco_custo 
        FROM vendas v 
        JOIN clientes c ON v.cliente_id = c.id 
        JOIN produtos p ON v.produto_id = p.id 
        WHERE {condicao_data} 
        ORDER BY v.data_venda DESC
    """, parametros_data).fetchall()
    
    despesas_total = conn.execute(f"SELECT SUM(valor) FROM despesas WHERE {condicao_despesa}", parametros_data).fetchone()[0] or 0.0
    
    faturamento = 0.0
    lucro_bruto_real = 0.0
    vendas_processadas = []
    
    for v in vendas_db:
        v_dict = dict(v)
        valor_total = v_dict.get('valor_total', 0.0)
        quantidade = v_dict.get('quantidade', 1)
        preco_custo = v_dict.get('preco_custo', 0.0)
        forma_pg = v_dict.get('forma_pagamento', 'Dinheiro')
        
        # Desconto de taxa automático por meio de pagamento no Lucro Real
        taxa_percentual = TAXAS_PAGAMENTO.get(forma_pg, 0.0)
        custo_financeiro_taxa = valor_total * taxa_percentual
        
        preco_custo_total = preco_custo * quantidade
        # O lucro real desconta o custo do produto e a taxa cobrada pela maquininha
        lucro_venda = valor_total - preco_custo_total - custo_financeiro_taxa
        
        faturamento += valor_total
        lucro_bruto_real += lucro_venda
        
        vendas_processadas.append({
            'id': v_dict.get('id'),
            'data_venda': v_dict.get('data_venda'),
            'cliente_nome': v_dict.get('cliente_nome'),
            'produto_nome': v_dict.get('produto_nome'),
            'quantidade': quantidade,
            'forma_pagamento': forma_pg,
            'desconto': v_dict.get('desconto', 0.0),
            'valor_total': valor_total,
            'lucro_real': lucro_venda
        })
        
    lucro_liquido = lucro_bruto_real - despesas_total
    
    # 2. INTELIGÊNCIA: Top 5 Produtos
    top_produtos_db = conn.execute(f"""
        SELECT p.nome, SUM(v.quantidade) as qtd_vendida 
        FROM vendas v 
        JOIN produtos p ON v.produto_id = p.id 
        WHERE {condicao_data} 
        GROUP BY p.id 
        ORDER BY qtd_vendida DESC LIMIT 5
    """, parametros_data).fetchall()
    top_produtos = [{'nome': row['nome'], 'qtd': row['qtd_vendida']} for row in top_produtos_db]
    
    # CRM: Classificação dos Clientes VIPs (Top 5 geradores de Faturamento)
    top_clientes_db = conn.execute(f"""
        SELECT c.nome, SUM(v.valor_total) as total_faturado
        FROM vendas v
        JOIN clientes c ON v.cliente_id = c.id
        WHERE {condicao_data}
        GROUP BY c.id
        ORDER BY total_faturado DESC LIMIT 5
    """, parametros_data).fetchall()
    top_clientes = [{'nome': row['nome'], 'faturamento': row['total_faturado']} for row in top_clientes_db]

    # Grafico de Linha (Evolução Diária)
    grafico_linha_db = conn.execute(f"""
        SELECT strftime('%Y-%m-%d', v.data_venda) as dia, SUM(v.valor_total) as total_dia 
        FROM vendas v 
        WHERE {condicao_data} 
        GROUP BY dia ORDER BY dia ASC
    """, parametros_data).fetchall()
    labels_evolucao = [row['dia'] for row in grafico_linha_db]
    valores_evolucao = [row['total_dia'] for row in grafico_linha_db]
    
    # Grafico de Pizza: Despesas por Categoria (Nova inteligência para despesas)
    grafico_pizza_db = conn.execute(f"""
        SELECT categoria, SUM(valor) as total
        FROM despesas 
        WHERE {condicao_despesa}
        GROUP BY categoria
    """, parametros_data).fetchall()
    labels_despesas = [row['categoria'] for row in grafico_pizza_db]
    valores_despesas = [row['total'] for row in grafico_pizza_db]
    
    conn.close()
    
    # Salva na sessão temporária para permitir exportação do filtro atual
    session['vendas_filtradas_exportar'] = vendas_processadas
    
    return render_template('relatorios.html', 
                           vendas=vendas_processadas, 
                           ticket=vendas_processadas, 
                           faturamento=faturamento, 
                           lucro=lucro_liquido, 
                           despesas_total=despesas_total, 
                           periodo_selecionado=periodo, 
                           top_produtos=top_produtos,
                           top_clientes=top_clientes, 
                           labels_evolucao=labels_evolucao, 
                           valores_evolucao=valores_evolucao, 
                           labels_pagamento=labels_despesas,  # Gráfico mudou para focar em categorias de despesas
                           valores_pagamento=valores_despesas, 
                           meta_mensal=META_MENSAL, 
                           meta_faturamento=faturamento_mes_atual, 
                           meta_porcentagem=porcentagem_meta, 
                           meta_barra=porcentagem_barra)


# -------------------------------------------------------------------------
# RECURSO DE EXPORTAÇÃO PARA CSV
# -------------------------------------------------------------------------
@app.route('/relatorios/exportar')
@requer_login
@requer_admin
def exportar_csv():
    vendas_lista = session.get('vendas_filtradas_exportar', [])
    
    # Criação do buffer em memória para o CSV
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    
    # Cabeçalho do arquivo CSV
    writer.writerow(['ID Venda', 'Data da Venda', 'Cliente', 'Produto', 'Qtd', 'Forma Pagamento', 'Desconto Aplicado (R$)', 'Faturamento Bruto (R$)', 'Lucro Real Calculado (R$)'])
    
    for v in vendas_lista:
        writer.writerow([
            v['id'],
            v['data_venda'],
            v['cliente_nome'],
            v['produto_nome'],
            v['quantidade'],
            v['forma_pagamento'],
            f"{v['desconto']:.2f}",
            f"{v['valor_total']:.2f}",
            f"{v['lucro_real']:.2f}"
        ])
    
    output.seek(0)
    
    # Retorna o arquivo gerado nativamente via Response do Flask com encoding UTF-8
    return Response(
        output.getvalue().encode('utf-8-sig'),
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=relatorio_vendas.csv"}
    )


# -------------------------------------------------------------------------
# AUTOMAÇÃO DE ESTOQUE: VISUALIZAÇÃO DE ORDENS DE COMPRA / COTAÇÕES FICTÍCIAS
# -------------------------------------------------------------------------
@app.route('/estoque/solicitar_cotacao/<int:produto_id>')
@requer_login
@requer_admin
def solicitar_cotacao(produto_id):
    conn = conectar_db()
    # Busca o produto específico cruzando com as informações do fornecedor associado
    produto_dados = conn.execute("""
        SELECT p.*, f.nome AS fornecedor_nome, f.contato AS fornecedor_contato
        FROM produtos p
        LEFT JOIN fornecedores f ON p.fornecedor_id = f.id
        WHERE p.id = ?
    """, (produto_id,)).fetchone()
    conn.close()
    
    if not produto_dados:
        flash("Produto não encontrado!")
        return redirect(url_for('dashboard'))
        
    if not produto_dados['fornecedor_nome']:
        flash(f"O produto '{produto_dados['nome']}' não possui nenhum fornecedor vinculado! Vincule um fornecedor primeiro.")
        return redirect(url_for('produtos'))
        
    # Gera o template visual de e-mail pronto (Ordem de Compra fictícia)
    return render_template('ordem_compra.html', produto=produto_dados, data_atual=datetime.now().strftime('%d/%m/%Y'))


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)