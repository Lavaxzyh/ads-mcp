"""Mark externally observed task boundaries without inventing missing durations."""
import argparse
import json
from workflow_trace import Trace,PHASES


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task-dir',required=True)
    sub=parser.add_subparsers(dest='action',required=True)
    begin=sub.add_parser('begin');begin.add_argument('phase',choices=PHASES);begin.add_argument('--label',default='')
    end=sub.add_parser('end');end.add_argument('span_id');end.add_argument('--outcome',choices=['completed','failed','cancelled'],default='completed')
    sub.add_parser('summary')
    args=parser.parse_args();trace=Trace(args.task_dir)
    if args.action=='begin':result={'span_id':trace.begin(args.phase,args.label,'caller_boundary'),'measurement':'boundary elapsed; includes waiting'}
    elif args.action=='end':trace.end(args.span_id,args.outcome);result=trace.summary()
    else:result=trace.summary()
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
