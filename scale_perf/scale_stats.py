import argparse
import sys

from monascaclient import client
from monascaclient.common import utils
from monascaclient import ksclient

# Reported processes
monitoring = ['monasca-api',
              'monasca-persister',
              'monasca-agent',
              'monasca-notification',
              'monasca-thresh',
              'kafka.Kafka',
              'org.apache.zookeeper.server',
              'vertica']

transform = ['monasca-transform',
             'pyspark',
             'transform/lib/driver',
             'org.apache.spark.executor.CoarseGrainedExecutorBackend',
             'org.apache.spark.deploy.master.Master',
             'org.apache.spark.deploy.worker.Worker']

logging = ['monasca-log-api',
           'kibana',
           'elasticsearch',
           'logstash',
           'beaver']

ceilometer = ['ceilometer-api',
              'ceilometer-polling',
              'ceilometer-agent-notification']

openstack = ['rabbitmq',
             'mysqld']

hos = ['haproxy']

keystone = {
    'username': utils.env('OS_USERNAME'),
    'password': utils.env('OS_PASSWORD'),
    'token': utils.env('OS_AUTH_TOKEN'),
    'auth_url': utils.env('OS_AUTH_URL'),
    'service_type': utils.env('OS_SERVICE_TYPE'),
    'endpoint_type': utils.env('OS_ENDPOINT_TYPE'),
    'os_cacert': utils.env('OS_CACERT'),
    'user_domain_id': utils.env('OS_USER_DOMAIN_ID'),
    'user_domain_name': utils.env('OS_USER_DOMAIN_NAME'),
    'project_id': utils.env('OS_PROJECT_ID'),
    'project_name': utils.env('OS_PROJECT_NAME'),
    'domain_id': utils.env('OS_DOMAIN_ID'),
    'domain_name': utils.env('OS_DOMAIN_NAME'),
    'region_name': utils.env('OS_REGION_NAME')
}

errors = []


class StatsResult(object):
    def __init__(self, data):
        self._data = data

    def select(self, key, value):
        result = []
        for stat in self._data:
            if stat['dimensions'][key] == value:
                result.append(stat)
        return StatsResult(result)

    @property
    def values(self):
        return self._data[0]['statistics'][0][1]


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('starttime', metavar='<UTC_START_TIME>',
                        help='metrics per second average >= UTC time. format: 2014-01-01T00:00:00Z.')
    parser.add_argument('endtime', metavar='<UTC_END_TIME>',
                        help='metrics per second average >= UTC time. format: 2014-01-01T00:00:00Z.')
    return parser.parse_args()


def get_mml_nodes():
    mon_api = mon_client.metrics.list(name='process.cpu_perc',
                                      dimensions={'process_name': 'monasca-api'})
    mml_nodes = []
    for metric in mon_api:
        mml_nodes.append(metric['dimensions']['hostname'])
    return sorted(mml_nodes)


def statistics(metric, stats_type, group_by):
    return StatsResult(mon_client.metrics.list_statistics(
                start_time=args.starttime,
                end_time=args.endtime,
                statistics=stats_type,
                name=metric,
                group_by=group_by,
                period=10000000))


def get_process_average():
    cpu = statistics('process.cpu_perc', 'avg', group_by='*')
    mem = statistics('process.mem.rss_mbytes', 'avg', group_by='*')
    return (cpu, mem)


def host_average(mml_nodes):
    cpu = statistics('cpu.idle_perc', 'avg', group_by='*')
    min_mem = statistics('mem.usable_mb', 'min', group_by='*')
    max_used = statistics('mem.used_mb', 'max', group_by='*')

    result = []
    for node in mml_nodes:
        try:
            result.append(
                [cpu.select('hostname', node).values,
                 min_mem.select('hostname', node).values,
                 max_used.select('hostname', node).values])
        except Exception:
            errors.append("Bad host data for node: {}".format(node))
            result.append([0, 0, 0])

    return result


def host_report(nodes):
    host_data = host_average(nodes)

    print("{:<30}| {:^8} | {:^8} | {:^8}".format("SYSTEM", "Node 1", "Node 2", "Node 3"))
    print("--------------------------------------------------------------")
    print("{:<30}| {:>8.2f} | {:>8.2f} | {:>8.2f}".format("idle %",
          host_data[0][0],
          host_data[1][0],
          host_data[2][0]))

    print("{:<30}| {:>8.2f} | {:>8.2f} | {:>8.2f}".format("min free",
          host_data[0][1]/1024,
          host_data[1][1]/1024,
          host_data[2][1]/1024))

    print("{:<30}| {:>8.2f} | {:>8.2f} | {:>8.2f}".format("max used",
          host_data[0][2]/1024,
          host_data[1][2]/1024,
          host_data[2][2]/1024))


def process_data(process, nodes, data, _type):
    result = []
    for node in nodes:
        try:
            result.append(data.select('hostname', node).select('process_name', process).values)
        except Exception:
            errors.append("Bad {} data for {} on node {}".format(_type, process, node))
            result.append(-1)
    return result


def process_group_report(processes, nodes, cpu, mem):
    total_cpu = {'node1': [], 'node2': [], 'node3': []}

    print("{:<30}| {:^8} | {:^8} | {:^8}".format("CPU", "Node 1", "Node 2", "Node 3"))
    print("--------------------------------------------------------------")
    for process in processes:
        try:
            n1, n2, n3 = process_data(process, nodes, cpu, "CPU")

            total_cpu['node1'].append(n1)
            total_cpu['node2'].append(n2)
            total_cpu['node3'].append(n3)

            # fix really long zookeeper name
            if process == 'org.apache.zookeeper.server':
                process = 'zookeeper'

            if process == 'kafka.Kafka':
                process = 'kafka'

            print("{:<30}| {:>8.2f} | {:>8.2f} | {:>8.2f}".format(process[:30], n1, n2, n3))
        except Exception:
            errors.append("Bad data for {}".format(process))

    print("--------------------------------------------------------------")
    print("{:<30}| {:>8.2f} | {:>8.2f} | {:8.2f}"
          .format('total',
                  sum(total_cpu['node1']),
                  sum(total_cpu['node2']),
                  sum(total_cpu['node3'])))

    total_mem = {'node1': [], 'node2': [], 'node3': []}
    print("\n")
    print("{:<30}| {:^8} | {:^8} | {:^8}".format("MEM", "Node 1", "Node 2", "Node 3"))
    print("--------------------------------------------------------------")
    for process in processes:
        try:
            n1, n2, n3 = process_data(process, nodes, mem, "MEM")

            total_mem['node1'].append(n1)
            total_mem['node2'].append(n2)
            total_mem['node3'].append(n3)

            # fix really long zookeeper name
            if process == 'org.apache.zookeeper.server':
                process = 'zookeeper'

            if process == 'kafka.Kafka':
                process = 'kafka'

            print("{:<30}| {:>8.2f} | {:>8.2f} | {:>8.2f}".format(process[:30], n1, n2, n3))
        except Exception:
            errors.append("Bad data for {}".format(process))

    print("--------------------------------------------------------------")
    print("{:<30}| {:>8.2f} | {:>8.2f} | {:8.2f}"
          .format('total',
                  sum(total_mem['node1']),
                  sum(total_mem['node2']),
                  sum(total_mem['node3'])))


def generate_report():
    mml_nodes = get_mml_nodes()

    host_report(mml_nodes)

    cpu, mem = get_process_average()

    if not cpu:
        print("No CPU data for processes")
        cpu = []

    if not mem:
        print("No CPU data for processes")
        mem = []

    for name, group in [('Monitoring', monitoring),
                        ('Transform', transform),
                        ('Logging', logging),
                        ('Ceilometer', ceilometer),
                        ('Openstack', openstack),
                        ('HOS', hos)]:
        print("\n")
        print("-- {} ------------------".format(name))
        process_group_report(group, mml_nodes, cpu, mem)

    print("\n")
    print("Errors:")
    for e in errors:
        print(e)


if __name__ == "__main__":
    args = parse_args()

    try:
        ks_client = ksclient.KSClient(**keystone)
    except Exception as ex:
        print('Failed to authenticate: {}'.format(ex))
        sys.exit(1)

    mon_client = client.Client('2_0', ks_client.monasca_url, token=ks_client.token)

    sys.exit(generate_report())
