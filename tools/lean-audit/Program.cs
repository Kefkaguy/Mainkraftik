using System.Text.Json;
using QuantConnect;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Interfaces;
using QuantConnect.Orders;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;
using QuantConnect.Securities.Equity;

var fixtures = JsonDocument.Parse(File.ReadAllText(args[0])).RootElement;
var results = new List<object>();
foreach (var fixture in fixtures.EnumerateArray()) {
    decimal cash = 5000, inventory = 0;
    var fills = new List<object>();
    foreach (var step in fixture.GetProperty("orders").EnumerateArray()) {
        var symbol = new Symbol(SecurityIdentifier.GenerateEquity("SPY", Market.USA, false), "SPY");
        var config = new SubscriptionDataConfig(typeof(TradeBar), symbol, Resolution.Minute,
            TimeZones.NewYork, TimeZones.NewYork, true, true, false);
        var quotes = new SubscriptionDataConfig(config, typeof(QuoteBar));
        var equity = new Equity(SecurityExchangeHours.AlwaysOpen(TimeZones.NewYork), config,
            new Cash(Currencies.USD, 0, 1m), SymbolProperties.GetDefault(Currencies.USD),
            ErrorCurrencyConverter.Instance, RegisteredSecurityDataTypesProvider.Null, Exchange.ARCA);
        var local = DateTime.Parse(step.GetProperty("time").GetString()!);
        var utc = local.ConvertToUtc(TimeZones.NewYork);
        var keeper = new TimeKeeper(utc, new[] {TimeZones.NewYork});
        equity.SetLocalTimeKeeper(keeper.GetLocalTimeKeeper(TimeZones.NewYork));
        var bid = step.GetProperty("bid").GetDecimal();
        var ask = step.GetProperty("ask").GetDecimal();
        equity.SetMarketPrice(new QuoteBar(local.AddMinutes(-1), symbol,
            new Bar(bid,bid,bid,bid), 100, new Bar(ask,ask,ask,ask), 100));
        var order = new MarketOrder(symbol, step.GetProperty("qty").GetDecimal(), utc);
        var fill = new EquityFillModel().Fill(new FillModelParameters(equity, order,
            new Provider(new List<SubscriptionDataConfig>{config, quotes}), Time.OneHour, null)).Single();
        cash -= fill.FillQuantity * fill.FillPrice;
        inventory += fill.FillQuantity;
        fills.Add(new {quantity=fill.FillQuantity, price=fill.FillPrice, status=fill.Status.ToString()});
    }
    results.Add(new {name=fixture.GetProperty("name").GetString(),cash,inventory,pnl=cash-5000,fills});
}
Console.WriteLine("AUDIT_JSON="+JsonSerializer.Serialize(results));
class Provider(List<SubscriptionDataConfig> configs) : ISubscriptionDataConfigProvider {
    public List<SubscriptionDataConfig> GetSubscriptionDataConfigs(Symbol symbol=null, bool includeInternalConfigs=false) => configs;
}
