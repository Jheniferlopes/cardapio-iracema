// Interações do Cardápio Iracema
(() => {
  const tipo = document.getElementById('tipo');
  const camposAluno = document.querySelectorAll('.aluno-field');
  const atualizarCampos = () => {
    if (!tipo) return;
    const exibir = tipo.value === 'Aluno';
    camposAluno.forEach((campo) => { campo.style.display = exibir ? 'flex' : 'none'; });
  };
  if (tipo) {
    tipo.addEventListener('change', atualizarCampos);
    atualizarCampos();
  }
  document.querySelectorAll('form.confirm-delete').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (!window.confirm('Tem certeza de que deseja excluir este registro? Esta ação não pode ser desfeita.')) {
        event.preventDefault();
      }
    });
  });
})();
