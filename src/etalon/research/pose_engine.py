"""Small frozen GNINA invocation over MolCascade's existing native runner."""
from pathlib import Path
import os


def command(executable, parameters, output, *, stage, pool=None):
    p=parameters
    if stage not in {'empirical','cnn'}:
        raise ValueError('unknown pose engine stage')
    if stage=='cnn' and pool is None:
        raise ValueError('CNN rescoring requires the unchanged existing pool')
    args=[str(executable),'-r',p['receptor'],'-l',p['ligand'] if stage=='empirical' else str(pool),
          '-o',str(output),'--cpu','4','--seed',str(p['seed'])]
    if stage=='empirical':
        args+=['--cnn_scoring','none','--exhaustiveness',str(p['exhaustiveness']),'--num_modes',str(p['num_modes'])]
        for dim,val in zip('xyz',p['box']['center']):args+=['--center_'+dim,str(val)]
        for dim,val in zip('xyz',p['box']['size']):args+=['--size_'+dim,str(val)]
    else:
        args+=['--score_only','--cnn_scoring','rescore','--cnn','crossdock_default2018','--device','0']
    return args


def execute(args, *, directory, gpu='1'):
    from molcascade.plugins.builtin.docking.common import run_engine
    if gpu != '1':raise ValueError('this pilot is bound to the explicitly available GPU 1')
    os.environ['CUDA_VISIBLE_DEVICES']=gpu
    return run_engine(args,cwd=Path(directory),timeout=600,engine='GNINA',code='PB24_GNINA',
                      hint='Inspect immutable request, native output and input identities')
